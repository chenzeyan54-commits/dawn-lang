package contract;

import java.io.IOException;
import java.lang.reflect.Method;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;

/** Count real method entries without adding host dependencies to shared Dawn. */
public final class SourceParseCounts {
    private static final long[] COUNTS = new long[3];
    private static final String OWNER = "source parse invocation counts";
    private static final String PARSER = "dawn$pkg$selfhost/front/parser";
    private static final String PROJECTION = "dawn$pkg$selfhost/check/source_projection";
    private static final String INDEXED = "L" + PROJECTION + "$IndexedData;";
    private static final Map<String, Integer> TARGETS = Map.of(
        PARSER + ".parse_module_lexed(Ljava/lang/String;)Ldawn/rt/Tuple4;", 0,
        PROJECTION + ".index(Ljava/lang/String;)" + INDEXED, 1,
        PROJECTION + ".index_cps(Ljava/lang/String;Lstd/pvec$Vec;)" + INDEXED, 1,
        PROJECTION + ".tokens_from(" + INDEXED + "Lstd/pvec$Vec;Lstd/pvec$Vec;)LOption;", 2);

    public static void hit(int index) { COUNTS[index]++; }
    /** External fixtures may exclude setup explicitly; never production code. */
    public static void begin() { Arrays.fill(COUNTS, 0); }

    private static final class ObservedLoader extends URLClassLoader {
        private final Set<String> seen = new HashSet<>();

        ObservedLoader(Path jar) throws IOException {
            super(new URL[]{jar.toUri().toURL()}, SourceParseCounts.class.getClassLoader());
        }

        @Override protected Class<?> findClass(String name) throws ClassNotFoundException {
            String internal = name.replace('.', '/');
            if (!internal.equals(PARSER) && !internal.equals(PROJECTION)) {
                return super.findClass(name);
            }
            URL resource = findResource(internal + ".class");
            if (resource == null) throw new ClassNotFoundException("Missing counter target " + name);
            try (var input = resource.openStream()) {
                ClassReader reader = new ClassReader(input.readAllBytes());
                ClassWriter writer = new ClassWriter(reader, ClassWriter.COMPUTE_MAXS);
                reader.accept(new ClassVisitor(Opcodes.ASM9, writer) {
                    @Override public MethodVisitor visitMethod(int access, String method, String descriptor,
                                                               String signature, String[] exceptions) {
                        MethodVisitor downstream = super.visitMethod(access, method, descriptor, signature, exceptions);
                        String key = internal + "." + method + descriptor;
                        Integer counter = TARGETS.get(key);
                        if (counter == null) return downstream;
                        if ((access & Opcodes.ACC_STATIC) == 0 || !seen.add(key)) {
                            throw new IllegalStateException("Invalid or repeated counter target " + key);
                        }
                        return new MethodVisitor(Opcodes.ASM9, downstream) {
                            @Override public void visitCode() {
                                super.visitCode();
                                super.visitLdcInsn(counter);
                                super.visitMethodInsn(Opcodes.INVOKESTATIC, "contract/SourceParseCounts",
                                                      "hit", "(I)V", false);
                            }
                        };
                    }
                }, 0);
                byte[] bytes = writer.toByteArray();
                return defineClass(name, bytes, 0, bytes.length);
            } catch (IOException e) {
                throw new ClassNotFoundException(name, e);
            }
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("Expected subject jar");
        String[] samples = {"clean_on", "clean_off", "recovered", "lexer_error", "delegated_of"};
        long[][] expected = {{1, 1, 1}, {1, 0, 0}, {1, 0, 0}, {1, 0, 0}, {1, 1, 1}};
        verify(Path.of(args[0]), "source_parse_counts", OWNER, samples, expected);
    }

    public static void verify(Path jar, String fixtureName, String owner,
                              String[] samples, long[][] expected) throws Exception {
        if (samples.length != expected.length) throw new IllegalArgumentException("Sample/count shape mismatch");
        // Independently execute the unmodified bytes before adding counters.
        // Each mutant must preserve all fixture outcomes, not just the first
        // sample whose measured count will reject it.
        try (URLClassLoader plain = new URLClassLoader(new URL[]{jar.toUri().toURL()},
                                                       SourceParseCounts.class.getClassLoader())) {
            Class<?> fixture = Class.forName(fixtureName, true, plain);
            for (String sample : samples) {
                if (!Boolean.TRUE.equals(fixture.getMethod(sample).invoke(null))) {
                    throw new AssertionError("Uninstrumented semantic sample failed " + sample);
                }
            }
        }
        try (ObservedLoader loader = new ObservedLoader(jar)) {
            Class.forName(PARSER.replace('/', '.'), true, loader);
            Class.forName(PROJECTION.replace('/', '.'), true, loader);
            Class<?> fixture = Class.forName(fixtureName, true, loader);
            if (!loader.seen.equals(TARGETS.keySet())) {
                throw new IllegalStateException("Counter targets drifted: " + loader.seen);
            }
            for (int index = 0; index < samples.length; index++) {
                Method sample = fixture.getMethod(samples[index]);
                if (sample.getReturnType() != boolean.class || sample.getParameterCount() != 0) {
                    throw new IllegalStateException("Fixture descriptor drifted: " + sample);
                }
                Arrays.fill(COUNTS, 0);
                Object valid = sample.invoke(null);
                if (!Boolean.TRUE.equals(valid)) throw new AssertionError("Semantic sample failed " + samples[index]);
                if (!Arrays.equals(COUNTS, expected[index])) {
                    System.out.println("FAIL  " + fixtureName + " :: " + owner);
                    System.out.println("  assertion failed: " + samples[index] + " expected=" +
                                       Arrays.toString(expected[index]) + " actual=" + Arrays.toString(COUNTS));
                    System.exit(1);
                }
                System.out.println("COUNT " + samples[index] + " " + Arrays.toString(COUNTS));
            }
            System.out.println("PASS  " + fixtureName + " :: " + owner);
        }
    }
}
