package world.sins.auth;

import java.util.Optional;
import java.util.UUID;

interface SkinLookup {
    Optional<String> texture(UUID uuid, String canonicalName) throws Exception;
}
