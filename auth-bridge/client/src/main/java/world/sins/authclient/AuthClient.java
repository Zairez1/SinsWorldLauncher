package world.sins.authclient;

import com.google.gson.JsonParser;
import io.netty.buffer.Unpooled;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.resolver.ServerAddress;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.protocol.game.ServerboundCustomPayloadPacket;
import net.minecraft.resources.ResourceLocation;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.client.event.ClientPlayerNetworkEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;

@Mod("sinsworld_auth_client")
public final class AuthClient {
    @Mod.EventBusSubscriber(modid = "sinsworld_auth_client", value = Dist.CLIENT)
    public static final class ClientEvents {
        @SubscribeEvent public static void connected(ClientPlayerNetworkEvent.LoggingIn event) {
            String configuredFile = System.getProperty("sinsworld.auth.file", "");
            if (configuredFile.isBlank()) return;
            try {
                Minecraft mc = Minecraft.getInstance();
                var server = mc.getCurrentServer();
                if (server == null) return; // Never present a ticket in single-player.
                Path root = mc.gameDirectory.toPath().toAbsolutePath().normalize().resolve(".sinsworld-auth");
                Path file = Path.of(configuredFile).toRealPath();
                if (!file.startsWith(root.toRealPath()) || !Files.isRegularFile(file) || Files.size(file) > 4096) return;
                var data = JsonParser.parseString(Files.readString(file)).getAsJsonObject();
                ServerAddress expected = ServerAddress.parseString(data.get("server").getAsString());
                ServerAddress actual = ServerAddress.parseString(server.ip);
                if (!expected.getHost().equalsIgnoreCase(actual.getHost()) || expected.getPort() != actual.getPort()) return;
                if (data.get("expires_at").getAsLong() <= System.currentTimeMillis()) { Files.deleteIfExists(file); return; }
                if (!mc.getUser().getGameProfile().getId().toString().equals(data.get("uuid").getAsString())) return;
                String token = data.get("ticket").getAsString();
                if (!token.matches("[A-Za-z0-9_-]{43}")) return;
                // Remove disk copy before the single transmission; the launcher obtains
                // a new ticket for every later game launch. No token/password in chat/logs.
                Files.delete(file);
                var payload = new FriendlyByteBuf(Unpooled.buffer(43));
                payload.writeBytes(token.getBytes(StandardCharsets.US_ASCII));
                event.getConnection().send(new ServerboundCustomPayloadPacket(new ResourceLocation("sinsworld", "auth"), payload));
            } catch (Exception ignored) {
                // Authentication remains closed in AuthMe; never fall back to a nick/IP login.
            }
        }
    }
}
