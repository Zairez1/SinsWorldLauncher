package world.sins.auth;

final class LauncherPolicy {
    static boolean accepts(String agent, String minimum) {
        try {
            if (agent == null || !agent.startsWith("SinsWorldLauncher/")) return false;
            int[] actual = version(agent.substring("SinsWorldLauncher/".length()));
            int[] required = version(minimum);
            for (int i = 0; i < 3; i++) {
                if (actual[i] != required[i]) return actual[i] > required[i];
            }
            return true;
        } catch (IllegalArgumentException e) { return false; }
    }
    private static int[] version(String value) {
        if (value == null || !value.matches("[0-9]{1,6}\\.[0-9]{1,6}\\.[0-9]{1,6}")) throw new IllegalArgumentException();
        return java.util.Arrays.stream(value.split("\\.")).mapToInt(Integer::parseInt).toArray();
    }
}
