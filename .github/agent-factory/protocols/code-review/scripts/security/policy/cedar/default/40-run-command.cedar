// 40-run-command.cedar — TUNABLE (ordinary commands allowed; destructive ops are forbidden by 10-destructive)
@id("base.run-command")
permit (principal == Agent::"session", action == Action::"RunCommand", resource);
