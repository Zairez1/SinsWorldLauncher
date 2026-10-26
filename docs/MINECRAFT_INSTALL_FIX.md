# Minecraft installation and native bridge repair

The production HTML policy omitted unsafe-eval. pywebview 6.1 creates native API functions with new Function; the policy blocked API creation before pywebviewready. The UI could fall back to browser preview, with no working native Play action. The existing mock-API test bypassed this generator and missed the defect.

The local page now permits unsafe-eval for this bridge. Other source restrictions remain. tools/test_native_bridge.cjs runs the actual installed pywebview API generator under the production HTML policy: the old policy must fail, the corrected policy must initialize and route Play through the simulated native transport. This is a bridge-JavaScript regression test, not a claim of testing the Windows GUI or server authentication.

Minecraft is prepared before mod synchronization. Progress callbacks are throttled to avoid flooding synchronous WebView calls from download worker threads. Errors remain available through get_launch_state and in launcher.log; the UI polls as a fallback for missed events. Game process output goes to minecraft-console.log. Both files are under %APPDATA%/SinsWorldLauncher.

A Forge profile is written before processors finish, so its presence alone no longer marks installation complete. Processor exit codes are checked and their output is retained in forge-install.log. A separate completion marker and actual launch classpath checks allow interrupted installation recovery. Missing Minecraft client files stop launch.

Windows CI builds the EXE, then uses --verify-game-install in an empty temporary directory. The production installer downloads Minecraft 1.20.1, its assets and libraries, private Java 17 and Forge 47.3.22. The check requires the client JAR, all indexed asset files, Forge completion marker and complete classpath before release publication. Full graphical gameplay and server login are outside this check.
