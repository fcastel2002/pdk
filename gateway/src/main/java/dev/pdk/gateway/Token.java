package dev.pdk.gateway;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;

/** Random API token, regenerated on every module startup and written to the gateway data directory. */
final class Token {
    static final String HEADER = "X-PDK-Token";
    static final String FILE_NAME = "pdk-gateway.token";

    private volatile byte[] value;
    private volatile Path file;

    void rotate(Path dataDirectory) throws IOException {
        byte[] random = new byte[32];
        new SecureRandom().nextBytes(random);
        String token = Base64.getUrlEncoder().withoutPadding().encodeToString(random);
        Path target = dataDirectory.resolve(FILE_NAME);
        Files.writeString(target, token, StandardCharsets.UTF_8);
        value = token.getBytes(StandardCharsets.UTF_8);
        file = target;
    }

    void revoke() {
        value = null;
        Path current = file;
        if (current != null) {
            try {
                Files.deleteIfExists(current);
            } catch (IOException ignored) {
                // The next startup overwrites it anyway.
            }
        }
    }

    boolean matches(String supplied) {
        byte[] current = value;
        return current != null && supplied != null
                && MessageDigest.isEqual(current, supplied.trim().getBytes(StandardCharsets.UTF_8));
    }
}
