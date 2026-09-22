#!/usr/bin/env python3
"""Check real JVM Session ownership through the unchanged Playground gateway.

Reuse the real WebSocket client and standalone fixture/oracle. An exec-only
private wrapper keeps compiler stderr attributable to one actual child PID;
neither protocol synthesis nor cross-client counter totals are evidence here.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import sys
import time

from lsp_stats import decode

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SUBJECTS = ("cold-plain", "prepared-plain", "cold-stats", "prepared-stats")
URI = "untitled:dawn-playground/prog.dawn"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


wire = module("gateway_session_wire", ROOT / "playground/test/lsp_contract.py")
matrix = module("gateway_session_matrix", HERE / "lsp-edit-matrix.py")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def validate_policy(name, metadata, jar_hash):
    if (metadata.get("mode") != ("Cold" if name.startswith("cold-") else "PreparedBodies") or
            metadata.get("instrumented") != name.endswith("-stats") or
            metadata.get("reparse_control") is not None or
            metadata.get("compiler_sha256") != jar_hash or not metadata.get("sources")):
        raise RuntimeError("configured child provenance does not match its claimed policy")


def validate_argv(command):
    if (not isinstance(command, list) or not all(isinstance(x, str) for x in command) or
            len(command) < 4 or command[-3] != "-jar" or command[-1] != "lsp" or
            Path(command[0]).name != "java" or not Path(command[0]).is_absolute()):
        raise RuntimeError("require explicit JVM executable and -jar compiler.jar lsp arguments")
    for option in command[1:-3]:
        if not (re.fullmatch(r"-X(?:ss|mx)[1-9][0-9]*[kKmMgG]", option) or
                option in {"-XX:+UseSerialGC", "--add-exports=java.base/jdk.internal.vm=ALL-UNNAMED"}):
            raise RuntimeError("unsupported JVM launch option")


def load_subjects(path):
    if any(os.environ.get(name) for name in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS")):
        raise RuntimeError("unset JVM option-injection environment variables for explicit child provenance")
    manifest = json.loads(path.read_text())
    if set(manifest) != set(SUBJECTS):
        raise RuntimeError("subject manifest must contain exactly four independent policies")
    result, sources = {}, None
    for name in SUBJECTS:
        item = manifest[name]
        command = item["argv"]
        validate_argv(command)
        jar = Path(command[-2]).resolve(strict=True)
        if not Path(command[-2]).is_absolute():
            raise RuntimeError("compiler jar path must be absolute")
        provenance = Path(item["metadata"]).resolve(strict=True)
        metadata = json.loads(provenance.read_text())
        validate_policy(name, metadata, digest(jar))
        if sources is None:
            sources = metadata["sources"]
        elif sources != metadata["sources"]:
            raise RuntimeError("configured children were built from different source inputs")
        dependencies = sorted((jar.parent / "lib").glob("*.jar"))
        fingerprints = {str(p.resolve()): digest(p) for p in [Path(command[0]), jar, provenance, *dependencies]}
        result[name] = {"argv": command, "metadata": str(provenance), "fingerprints": fingerprints,
                        "builder": metadata}
    return result


def exec_child(path):
    launch = json.loads(path.read_text())
    pid = os.getpid()
    directory = Path(launch["logs"])
    with (directory / f"{pid}.stderr").open("xb", buffering=0) as stream:
        os.dup2(stream.fileno(), 2)
    pending = directory / f"{pid}.pending"
    with pending.open("x") as stream:
        json.dump({"pid": pid, "argv": launch["argv"]}, stream)
    pending.replace(directory / f"{pid}.json")
    os.execvpe(launch["argv"][0], launch["argv"], os.environ.copy())


def process_identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except FileNotFoundError:
        return None


def own_client(used, label, pid, session):
    if label in used or any(owner["pid"] == pid or owner["session"] == session for owner in used.values()):
        raise RuntimeError("client borrowed another child's ownership")
    used[label] = {"pid": pid, "session": session}


def validate_epoch(publications, version, error):
    if (len(publications) != 1 or publications[0].get("uri") != URI or
            type(publications[0].get("version")) is not int or publications[0].get("version") != version):
        raise RuntimeError("missing, duplicate, wrong-URI or stale gateway publication")
    diagnostics = publications[0].get("diagnostics")
    if not isinstance(diagnostics, list):
        raise RuntimeError("missing gateway diagnostic array")
    if error:
        if len(diagnostics) != 1 or "benchmark_type_error" not in diagnostics[0].get("message", ""):
            raise RuntimeError("gateway lost the injected error or added an unrelated error")
    elif diagnostics:
        raise RuntimeError("clean gateway revision retained diagnostics")


def validate_counts(owner, actual_owner, lines, subject, expected):
    if owner != actual_owner:
        raise RuntimeError("counter log belongs to another client")
    counts = [decode(line) for line in lines.splitlines() if line.startswith("LSP_BODY_STATS\t")]
    if subject.endswith("-plain"):
        if counts:
            raise RuntimeError("plain child emitted private counters")
    elif subject == "cold-stats":
        if counts != [{"scope": "standalone", "observed": False, "counts": None}]:
            raise RuntimeError("cold standalone work must be explicitly unobserved")
    else:
        matrix.validate_counts(counts, expected)
    return counts


class Client:
    def __init__(self, gateway, label):
        self.gateway, self.label = gateway, label
        before = set(gateway.children.glob("*.json"))
        stream, response = wire.upgrade_when_available(gateway.port, timeout=10)
        if not response.startswith(b"HTTP/1.1 101 "):
            raise RuntimeError("gateway refused the real compiler session")
        stream.settimeout(15)
        self.ws = wire.WebSocket(stream)
        self.closed, self.request_id, self.last_send = False, 10, 0.0
        gateway.clients.append(self)
        wire.wait_until(lambda: len(set(gateway.children.glob("*.json")) - before) == 1, timeout=10)
        identity_path = (set(gateway.children.glob("*.json")) - before).pop()
        child = json.loads(identity_path.read_text())
        self.pid = child["pid"]
        if child["argv"] != gateway.subject["argv"]:
            raise RuntimeError("exec wrapper launched a different child")
        self.log = gateway.children / f"{self.pid}.stderr"
        def session_started():
            matches = re.findall(r"session=(\S+) child-start pid=" + str(self.pid) + r"\b", gateway.log.read_text())
            return len(matches) == 1
        wire.wait_until(session_started, timeout=10)
        self.session = re.findall(r"session=(\S+) child-start pid=" + str(self.pid) + r"\b", gateway.log.read_text())[0]
        own_client(gateway.owners, label, self.pid, self.session)
        save(gateway.output / "owners.json", gateway.owners)
        wire.initialize(self.ws)
        actual_command = Path(f"/proc/{self.pid}/cmdline").read_bytes().rstrip(b"\0").decode().split("\0")
        if actual_command != gateway.subject["argv"]:
            raise RuntimeError("gateway child PID is not the actual configured JVM process")
        self.identity = process_identity(self.pid)
        if self.identity is None:
            raise RuntimeError("configured child did not remain alive")
        self.offset = self.log.stat().st_size
        self.text, self.version, self.prefix = "", 0, ""

    def send(self, message):
        # Stay below the unchanged gateway's ten-message/second limit.
        time.sleep(max(0, 0.11 - (time.monotonic() - self.last_send)))
        self.ws.send_json(message)
        self.last_send = time.monotonic()

    def queries(self):
        publications, replies = [], {}
        for needle in (self.prefix + "provider()\n", self.prefix + "value_0(1)"):
            replies[needle] = {}
            for method in ("hover", "definition", "completion"):
                self.request_id += 1
                self.send(wire.rpc(self.request_id, "textDocument/" + method, {
                    "textDocument": {"uri": URI}, "position": matrix.position(self.text, needle)}))
                while True:
                    frame = self.ws.recv_json()
                    if frame.get("method") == "textDocument/publishDiagnostics":
                        publications.append(frame["params"])
                    elif frame.get("id") == self.request_id and "result" in frame and "error" not in frame:
                        replies[needle][method] = frame["result"]
                        break
                    else:
                        raise RuntimeError("unexpected gateway protocol response")
            if not replies[needle]["hover"] or not replies[needle]["definition"]:
                raise RuntimeError("gateway query target was not resolved")
        return publications, replies

    def counters(self, expected):
        child = json.loads((self.gateway.children / f"{self.pid}.json").read_text())
        with self.log.open("rb") as stream:
            stream.seek(self.offset)
            text = stream.read().decode("utf-8")
            self.offset = stream.tell()
        return validate_counts(self.pid, child["pid"], text, self.gateway.name, expected)

    def revision(self, label, text, error, expected, version=None, prefix=""):
        initial = self.version == 0
        self.version = version if version is not None else self.version + 1
        self.text, self.prefix = text, prefix
        note = (matrix.did_open(URI, text, self.version) if initial else
                matrix.did_change(URI, text, self.version))
        self.send(note)
        publications, replies = self.queries()
        validate_epoch(publications, self.version, error)
        counts = self.counters(expected)
        row = {"client": self.label, "label": label, "version": self.version,
               "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
               "diagnostics": publications, "replies": replies, "counts": counts,
               "owner": {"pid": self.pid, "session": self.session}}
        self.gateway.rows.append(row)
        save(self.gateway.output / "samples.json", self.gateway.rows)
        self.last_replies = replies

    def unchanged(self):
        publications, replies = self.queries()
        if publications or replies != self.last_replies:
            raise RuntimeError("one client's edit changed its idle peer's protocol state")
        if self.log.stat().st_size != self.offset:
            raise RuntimeError("idle peer unexpectedly performed analysis or emitted stderr")

    def close(self):
        if self.closed:
            return
        self.ws.close()
        self.closed = True
        wire.wait_until(lambda: process_identity(self.pid) != self.identity, timeout=10)


class Gateway:
    def __init__(self, output, name, subject):
        self.output, self.name, self.subject = output, name, subject
        output.mkdir()
        self.children = output / "children"
        self.children.mkdir()
        self.log = output / "gateway.log"
        self.clients, self.owners, self.rows = [], {}, []
        self.port = wire.free_port()
        launch = output / "launch.json"
        save(launch, {"argv": subject["argv"], "logs": str(self.children)})
        environment = os.environ.copy()
        environment.update({"PLAY_LSP_HOST": "127.0.0.1", "PLAY_LSP_PORT": str(self.port),
                            "PLAY_LSP_ORIGINS": wire.ORIGIN, "PLAY_LSP_MAX_SESSIONS": "2",
                            "PLAY_LSP_SOURCE_BYTES": "65536", "PLAY_LSP_MESSAGE_BYTES": "262144",
                            "PLAY_LSP_IDLE_SECONDS": "120", "PLAY_LSP_LIFETIME_SECONDS": "300",
                            "PLAY_LSP_SETUP_SECONDS": "15", "PLAY_LSP_HANDSHAKE_SECONDS": "5",
                            "PLAY_LSP_PING_SECONDS": "30", "PLAY_LSP_SHUTDOWN_SECONDS": "2",
                            "PLAY_LSP_UNSAFE_LOCAL": "1", "PLAY_LSP_CHILD": shlex.join([
                                sys.executable, "-B", str(Path(__file__).resolve()), "--exec-child", str(launch)])})
        with self.log.open("wb") as stream:
            self.proc = subprocess.Popen([sys.executable, "-B", wire.GATEWAY], cwd=ROOT,
                                         env=environment, stdout=stream, stderr=subprocess.STDOUT,
                                         start_new_session=True)
        def listening():
            if self.proc.poll() is not None:
                raise RuntimeError("real gateway exited before listening")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return True
            except OSError:
                return False
        try:
            wire.wait_until(listening, timeout=10)
        except BaseException:
            self.finish()
            raise

    def finish(self):
        for client in self.clients:
            if not client.closed:
                try:
                    client.ws.stream.close()
                except OSError:
                    pass
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        survivors = []
        for client in self.clients:
            if hasattr(client, "identity") and process_identity(client.pid) == client.identity:
                try:
                    os.killpg(client.pid, signal.SIGKILL)
                    survivors.append(client.pid)
                except ProcessLookupError:
                    pass
        if survivors:
            raise RuntimeError(f"gateway failed to terminate owned compiler children: {survivors}")


def exercise(output, name, subject, size):
    gateway = Gateway(output, name, subject)
    try:
        cases = matrix.revisions(size)
        expected = matrix.expected_counts(size)
        a = Client(gateway, "A")
        for label, text, error in cases:
            a.revision(label, text, error, expected[label])
        b = Client(gateway, "B")
        for index, (label, text, error) in enumerate(cases):
            disjoint = text.replace("provider", "other_provider").replace("dependent", "other_dependent").replace("value_", "other_value_")
            b.revision(label, disjoint, error, expected[label], version=101 + index, prefix="other_")
            a.unchanged()
        a.revision("after-peer-edit", cases[1][1], False, expected["whitespace"])
        b.unchanged()
        a.close()
        b.unchanged()
        c = Client(gateway, "A-reconnected")
        c.revision("reconnected-initial", cases[0][1], False, expected["initial"])
        b.unchanged()
        c.revision("reconnected-warm", cases[1][1], False, expected["whitespace"])
        c.close()
        b.close()
        save(output / "owners.json", gateway.owners)
        semantic = [{key: value for key, value in row.items() if key not in {"owner", "counts"}}
                    for row in gateway.rows]
        save(output / "semantic.json", semantic)
        return semantic
    finally:
        gateway.finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subjects", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--functions", type=int, default=20)
    parser.add_argument("--evidence-label", choices=("canonical", "local-smoke"), default="canonical")
    args = parser.parse_args()
    if args.functions < 2:
        parser.error("require at least two functions")
    subjects = load_subjects(args.subjects)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    metadata = {"backend": "JVM", "evidence_label": args.evidence_label, "complete": False,
                "http_check_tested": False, "native_sandbox_tested": False, "timing_evidence": False,
                "subjects": subjects, "functions": args.functions, "manifest_sha256": digest(args.subjects),
                "gateway_sha256": digest(Path(wire.GATEWAY)), "runner_sha256": digest(Path(__file__))}
    save(output / "metadata.json", metadata)
    baseline = None
    for name, subject in subjects.items():
        for path, expected_hash in subject["fingerprints"].items():
            if digest(Path(path)) != expected_hash:
                raise RuntimeError("fingerprinted child input changed")
        actual = exercise(output / name, name, subject, args.functions)
        if baseline is None:
            baseline = actual
        elif actual != baseline:
            raise RuntimeError("real gateway cold/prepared or plain/observed semantic mismatch")
        print(f"PASS: real gateway JVM {name}; revisions, peer isolation and reconnect", flush=True)
    metadata["complete"] = True
    save(output / "metadata.json", metadata)
    print("OK: real Playground gateway JVM Session contract; not HTTP /check or native acceptance")


def selftest():
    clean = {"uri": URI, "version": 7, "diagnostics": []}
    validate_epoch([clean], 7, False)
    expected = (2, 3, 0, 0)
    fields = ("checked_bodies", "reused_bodies", "reused_modules", "cold_rejected_bodies")
    from lsp_stats import FIELDS
    values = dict(zip(fields, expected))
    observed = "LSP_BODY_STATS\tstandalone\t" + "\t".join(str(values.get(field, 0)) for field in FIELDS)
    validate_counts(12, 12, observed, "prepared-stats", expected)
    refused = 0
    tests = [lambda: validate_epoch([], 7, False), lambda: validate_epoch([clean, clean], 7, False),
             lambda: validate_epoch([{**clean, "uri": "untitled:foreign"}], 7, False),
             lambda: validate_epoch([clean], 8, False), lambda: validate_epoch([clean], 7, True),
             lambda: validate_counts(12, 13, observed, "prepared-stats", expected),
             lambda: validate_counts(12, 12, "", "prepared-stats", expected),
             lambda: validate_counts(12, 12, observed + "\n" + observed, "prepared-stats", expected),
             lambda: validate_counts(12, 12, observed, "cold-stats", expected),
             lambda: validate_counts(12, 12, observed, "prepared-plain", expected),
             lambda: own_client({"A": {"pid": 12, "session": "one"}}, "B", 12, "two"),
             lambda: own_client({"A": {"pid": 12, "session": "one"}}, "B", 13, "one")]
    good = {"mode": "Cold", "instrumented": False, "reparse_control": None,
            "compiler_sha256": "hash", "sources": {"source": "hash"}}
    validate_policy("cold-plain", good, "hash")
    tests.extend((lambda: validate_policy("prepared-plain", good, "hash"),
                  lambda: validate_policy("cold-stats", good, "hash"),
                  lambda: validate_policy("cold-plain", good, "other"),
                  lambda: validate_epoch([{**clean, "version": True}], 1, False),
                  lambda: validate_argv(["python3", "fake.py"]),
                  lambda: validate_argv(["/jdk/java", "-Xbootclasspath/a:/untracked.jar", "-jar", "/compiler.jar", "lsp"])))
    validate_argv(["/jdk/java", "-Xss512m", "-Xmx2g", "-XX:+UseSerialGC", "-jar", "/compiler.jar", "lsp"])
    for test in tests:
        try:
            test()
        except RuntimeError:
            refused += 1
            continue
        raise AssertionError("invalid gateway Session evidence accepted")
    print(f"OK: real gateway Session oracles; {refused} strict rejection cases; no compiler launched")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        selftest()
    elif len(sys.argv) == 3 and sys.argv[1] == "--exec-child":
        exec_child(Path(sys.argv[2]))
    else:
        main()
