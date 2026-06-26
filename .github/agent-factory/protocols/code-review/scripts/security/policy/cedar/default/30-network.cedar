// 30-network.cedar — TUNABLE (egress only to allowlisted hosts)
@id("net.egress-allowlist")
permit (principal == Agent::"session", action == Action::"Network", resource)
when { context.allowed_hosts.contains(context.host) };
