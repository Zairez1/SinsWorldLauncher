# Game window lifecycle and FerriteCore

After a successful Minecraft process start, the existing launcher window hides. The launcher process remains alive and waits for that same Java process. On normal exit or a crash, the existing window is shown and restored and the existing game_exited UI event resumes the launcher music. Explicit launcher shutdown prevents the watcher from reopening a closed window. Failed process creation never hides the launcher. Monitoring errors also restore the window and retain a diagnostic log.

Regression checks run real child processes returning 0 and 1, verify hide/show/restore order, verify explicit shutdown and simulate a failed process wait. The native Windows window operations still depend on pywebview/WebView2; these lifecycle unit tests use a simulated window.

The supplied ferritecore-6.0.1-forge-1.20.1.jar is 123034 bytes, SHA-256 9c2c9396a49e796d88497758caa4637d2bcbb433c318e2dd9cebcffbaf0f6c54. Official Modrinth version DG5Fn9Sz supplies byte-identical content; the workflow fetches that mirror, verifies the canonical hash and publishes the user's filename. Metadata supports Minecraft [1.20,1.21) and Forge [46.0.1,). There are now 44 canonical JARs. The existing mandatory synchronization installs the new mod on Play.
