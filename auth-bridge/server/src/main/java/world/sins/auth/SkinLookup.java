package world.sins.auth;

import java.util.Optional;
import java.util.UUID;
import java.util.Map;

interface SkinLookup {
    Optional<String> texture(UUID uuid, String canonicalName) throws Exception;
    default Map<String,String> signedSkin(UUID uuid,String name) throws Exception {return Map.of();}
    default String change(UUID uuid,String name,SkinChangeInput input) throws Exception {throw new UnsupportedOperationException();}
    default void saveShared(UUID uuid,Map<String,String> signed) throws Exception {}
    default void applyShared(Object player,Map<String,String> signed) throws Exception {}
}
