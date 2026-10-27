package world.sins.auth;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.security.*;
import java.security.cert.CertificateFactory;
import javax.net.ssl.*;
import java.net.*;
import java.net.http.*;
import com.sun.net.httpserver.*;

class DirectTlsTest {
    @TempDir Path directory;
    @Test void shadedJarCanCreateItsOwnTlsIdentity() throws Exception {
        Path jar = Path.of("build/libs/SinsWorldAuth-1.0.0.jar").toAbsolutePath();
        assertTrue(Files.isRegularFile(jar));
        Path classes = Path.of(DirectTlsPackagedProbe.class.getProtectionDomain().getCodeSource().getLocation().toURI());
        String classpath = jar + java.io.File.pathSeparator + classes;
        Path output = directory.resolve("probe-output.txt");
        var process = new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                "-cp", classpath, DirectTlsPackagedProbe.class.getName(), directory.resolve("identity").toString())
                .redirectErrorStream(true).redirectOutput(output.toFile()).start();
        assertTrue(process.waitFor(30, java.util.concurrent.TimeUnit.SECONDS));
        assertEquals(0, process.exitValue(), Files.readString(output));
        assertTrue(Files.readString(output).contains("reload OK"));
    }
    @Test void certificatePersistsAndOnlyTrustedClientCanConnect() throws Exception {
        var serverContext = DirectTls.create(directory, "127.0.0.1");
        var publicFile = directory.resolve("tls-certificate.pem");
        String pem = Files.readString(publicFile);
        assertFalse(pem.contains("PRIVATE KEY"));
        DirectTls.create(directory, "127.0.0.1");
        assertEquals(pem, Files.readString(publicFile));
        assertThrows(Exception.class, () -> DirectTls.create(directory, "127.0.0.2"));
        var trust = KeyStore.getInstance("PKCS12"); trust.load(null, null);
        try (var input = Files.newInputStream(publicFile)) {
            trust.setCertificateEntry("server", CertificateFactory.getInstance("X.509").generateCertificate(input));
        }
        var factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()); factory.init(trust);
        var trusted = SSLContext.getInstance("TLS"); trusted.init(null, factory.getTrustManagers(), null);
        HttpsServer server = HttpsServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setHttpsConfigurator(new HttpsConfigurator(serverContext));
        server.createContext("/health", exchange -> {
            byte[] bytes = "ok".getBytes(); exchange.sendResponseHeaders(200, bytes.length);
            try (var output = exchange.getResponseBody()) { output.write(bytes); }
        });
        server.start();
        try {
            int port = server.getAddress().getPort();
            var request = HttpRequest.newBuilder(URI.create("https://127.0.0.1:" + port + "/health")).build();
            var client = HttpClient.newBuilder().sslContext(trusted).build();
            assertEquals("ok", client.send(request, HttpResponse.BodyHandlers.ofString()).body());
            assertThrows(SSLHandshakeException.class, () -> HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.ofString()));
            var wrongName = HttpRequest.newBuilder(URI.create("https://localhost:" + port + "/health")).build();
            assertThrows(SSLHandshakeException.class, () -> client.send(wrongName, HttpResponse.BodyHandlers.ofString()));
        } finally { server.stop(0); }
    }
}
