// 90-baseline.cedar — TUNABLE scoped permit (least privilege)
@id("base.ordinary-repo-io")
permit (principal == Agent::"session", action in [Action::"ReadFile", Action::"WriteFile"], resource)
when { context.in_repo };
