package world.sins.auth;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class SkinTexturesTest {
    @Test void restrictsTextureHostAndNormalizesTls() {
        String url = "http://textures.minecraft.net/texture/" + "a".repeat(64);
        assertEquals(url.replace("http:", "https:"), SkinTextures.canonical(url).orElseThrow());
        for (String bad : new String[]{"https://localhost/skin",url+"?redirect=x",url.replace("minecraft.net", "minecraft.net.evil.test"),url.replace("textures.minecraft.net", "secret@textures.minecraft.net"),"file:///skin.png"})
            assertTrue(SkinTextures.canonical(bad).isEmpty());
    }
}
