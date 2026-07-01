// 10-destructive.cedar — LOCKED (OWASP ASI02 destructive ops)
@id("locked.no-destructive")
forbid (principal, action in [Action::"RunCommand", Action::"DeletePath"], resource)
when { context.destructive };
