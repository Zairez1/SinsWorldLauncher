package world.sins.auth;

import java.util.Optional;
import java.util.UUID;
import net.skinsrestorer.api.SkinsRestorer;
import net.skinsrestorer.api.SkinsRestorerProvider;
import net.skinsrestorer.api.PropertyUtils;

/** Loaded only after the optional SkinsRestorer plugin has enabled. */
final class SkinsRestorerLookup implements SkinLookup {
    private final SkinsRestorer api = SkinsRestorerProvider.get();

    @Override public Optional<String> texture(UUID uuid, String canonicalName) throws Exception {
        // HTTP worker thread, never the Bukkit main thread. Match this AuthMe server's offline UUID.
        var property = api.getPlayerStorage().getSkinForPlayer(uuid, canonicalName, false);
        return property.flatMap(p -> SkinTextures.canonical(PropertyUtils.getSkinTextureUrl(p)));
    }
}
