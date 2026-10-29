package world.sins.auth;

import java.util.Optional;

final class SkinTextures {
    private SkinTextures() {}
    static Optional<String> canonical(String value) {
        if (value == null || !value.matches("https?://textures\\.minecraft\\.net/texture/[0-9a-fA-F]{64}"))
            return Optional.empty();
        return Optional.of("https://" + value.substring(value.indexOf("://") + 3));
    }
}
