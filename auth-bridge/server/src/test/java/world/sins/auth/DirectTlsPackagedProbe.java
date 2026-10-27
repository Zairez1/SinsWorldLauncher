package world.sins.auth;
import java.nio.file.Path;
/** Executed in an isolated JVM with only the shaded production JAR and test classes. */
public final class DirectTlsPackagedProbe {
    public static void main(String[] args) throws Exception {
        DirectTls.create(Path.of(args[0]), "127.0.0.1");
        DirectTls.create(Path.of(args[0]), "127.0.0.1");
        System.out.println("Packaged TLS identity generation and reload OK");
    }
}
