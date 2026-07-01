// 20-scope.cedar — TUNABLE (least-agency: writes stay in the changed-file set)
@id("scope.writes-in-changed-set")
forbid (principal, action == Action::"WriteFile", resource)
unless { context.in_changed_set };
