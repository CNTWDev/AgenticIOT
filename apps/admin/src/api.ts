export type Probe = {
  status: "ok" | "unavailable";
  message: string;
  version?: string;
  traceId?: string;
};

export async function probe(path: string, expected: string): Promise<Probe> {
  try {
    const response = await fetch(`/api${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(7000),
    });
    const data: unknown = await response.json();
    const body =
      data && typeof data === "object" ? (data as Record<string, unknown>) : {};
    if (!response.ok || body.status !== expected) {
      return {
        status: "unavailable",
        message: path.endsWith("ready")
          ? "数据库连接或迁移尚未就绪"
          : "服务当前不可用",
        traceId: response.headers.get("x-trace-id") ?? undefined,
      };
    }
    return {
      status: "ok",
      message:
        expected === "ready"
          ? "连接正常 · 数据库版本匹配"
          : "服务已启动 · 请求可达",
      version: typeof body.version === "string" ? body.version : undefined,
    };
  } catch {
    return {
      status: "unavailable",
      message: "连接失败，请检查后台服务是否启动",
    };
  }
}
