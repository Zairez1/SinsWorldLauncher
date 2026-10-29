package world.sins.auth;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class LauncherPolicyTest {
 @Test void rejectsOldOrUnknownLaunchersAndComparesVersionsNumerically(){
  assertTrue(LauncherPolicy.accepts("SinsWorldLauncher/1.2.5","1.2.5"));
  assertTrue(LauncherPolicy.accepts("SinsWorldLauncher/1.10.0","1.2.5"));
  for(String agent:new String[]{null,"","SinsWorldLauncher/1.2.4","SinsWorldLauncher/1.2.5 extra","Other/99.0.0"})
   assertFalse(LauncherPolicy.accepts(agent,"1.2.5"));
 }
}
