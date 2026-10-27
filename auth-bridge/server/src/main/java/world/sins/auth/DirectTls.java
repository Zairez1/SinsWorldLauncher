package world.sins.auth;

import javax.net.ssl.*;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.*;
import java.security.cert.X509Certificate;
import java.math.BigInteger;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.bouncycastle.asn1.x500.X500Name;
import org.bouncycastle.asn1.x509.*;
import org.bouncycastle.cert.jcajce.*;
import org.bouncycastle.operator.jcajce.JcaContentSignerBuilder;

/** Local TLS identity. Private material never belongs in release assets or logs. */
final class DirectTls {
    static SSLContext create(Path directory, String ip) throws Exception {
        if (!ip.matches("[0-9]{1,3}(\\.[0-9]{1,3}){3}")) throw new IllegalArgumentException("tls-ip must be IPv4");
        for (String part : ip.split("\\.")) if (Integer.parseInt(part) > 255) throw new IllegalArgumentException("Invalid tls-ip");
        Files.createDirectories(directory);
        Path keyFile = directory.resolve("private-tls.p12");
        KeyStore store = KeyStore.getInstance("PKCS12");
        char[] password = new char[0];
        if (Files.exists(keyFile)) {
            try (var input = Files.newInputStream(keyFile)) { store.load(input, password); }
        } else {
            store.load(null, password);
            KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA"); generator.initialize(3072);
            KeyPair key = generator.generateKeyPair();
            Instant now = Instant.now();
            X500Name subject = new X500Name("CN=SinsWorld Auth");
            var builder = new JcaX509v3CertificateBuilder(subject, new BigInteger(159, new SecureRandom()).add(BigInteger.ONE),
                Date.from(now.minus(5, ChronoUnit.MINUTES)), Date.from(now.plus(1825, ChronoUnit.DAYS)), subject, key.getPublic());
            builder.addExtension(Extension.subjectAlternativeName, false, new GeneralNames(new GeneralName(GeneralName.iPAddress, ip)));
            builder.addExtension(Extension.basicConstraints, true, new BasicConstraints(false));
            builder.addExtension(Extension.keyUsage, true, new KeyUsage(KeyUsage.digitalSignature | KeyUsage.keyEncipherment));
            builder.addExtension(Extension.extendedKeyUsage, false, new ExtendedKeyUsage(KeyPurposeId.id_kp_serverAuth));
            X509Certificate cert = new JcaX509CertificateConverter().getCertificate(builder.build(new JcaContentSignerBuilder("SHA256withRSA").build(key.getPrivate())));
            cert.verify(key.getPublic());
            store.setKeyEntry("server", key.getPrivate(), password, new java.security.cert.Certificate[]{cert});
            Path temp = Files.createTempFile(directory, "private-tls-", ".tmp");
            try {
                restrict(temp);
                try (var output = Files.newOutputStream(temp)) { store.store(output, password); }
                Files.move(temp, keyFile, StandardCopyOption.ATOMIC_MOVE);
            } finally { Files.deleteIfExists(temp); }
        }
        restrict(keyFile);
        X509Certificate cert = (X509Certificate) store.getCertificate("server");
        cert.checkValidity();
        boolean match = cert.getSubjectAlternativeNames().stream().anyMatch(s -> Integer.valueOf(7).equals(s.get(0)) && ip.equals(s.get(1)));
        if (!match) throw new IllegalStateException("TLS identity belongs to another IP; keep a backup and reconfigure its certificate");
        String pem = "-----BEGIN CERTIFICATE-----\n" + Base64.getMimeEncoder(64, new byte[]{10}).encodeToString(cert.getEncoded()) + "\n-----END CERTIFICATE-----\n";
        Files.writeString(directory.resolve("tls-certificate.pem"), pem);
        KeyManagerFactory keys = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()); keys.init(store, password);
        SSLContext context = SSLContext.getInstance("TLS"); context.init(keys.getKeyManagers(), null, new SecureRandom());
        return context;
    }
    private static void restrict(Path file) throws Exception {
        try { Files.setPosixFilePermissions(file, PosixFilePermissions.fromString("rw-------")); }
        catch (UnsupportedOperationException ignored) { /* Windows ACLs are managed by the host. */ }
    }
}
