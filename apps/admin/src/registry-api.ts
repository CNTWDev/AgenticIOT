export type SessionInfo = {
  subject_ref: string;
  domain_ref: string;
  role: "operator" | "viewer";
  permissions: string[];
};
export type Page<T> = { items: T[]; next_cursor: string | null };
export type Capabilities = {
  properties: Record<
    string,
    {
      schema: Record<string, unknown>;
      readable: boolean;
      writable: boolean;
      unit?: string | null;
    }
  >;
  actions: Record<
    string,
    {
      input_schema: Record<string, unknown>;
      output_schema?: Record<string, unknown> | null;
      risk: string;
      confirmation: string;
      offline_policy?: string;
    }
  >;
  events: Record<string, { data_schema: Record<string, unknown> }>;
};
export type Model = Capabilities & {
  id: string;
  key: string;
  version: string;
  title: string;
  description: string;
  domain_ref: string;
  created_at: string;
};
export type Device = {
  id: string;
  model_id: string;
  model_version: string;
  external_ref: string;
  title: string;
  space_ref?: string | null;
  domain_ref: string;
  revision: number;
  lifecycle_status: string;
  reachability: string;
  created_at: string;
};
export type Thing = Device & Capabilities;
export type Audit = {
  id: string;
  subject_ref: string;
  operation: string;
  resource_id: string;
  trace_id: string;
  occurred_at: string;
};

const messages: Record<string, string> = {
  unsupported_model: "型号不兼容，请使用完整的虚拟灯具模板。",
  device_not_bound: "设备尚未绑定虚拟 Edge 或未启用。",
  binding_conflict: "设备已绑定其他 Edge，本阶段不支持改绑。",
  idempotency_conflict: "幂等键已用于其他请求，请核对请求内容。",
  invalid_action_input: "动作参数不符合能力定义。",
  invalid_deadline: "截止时间必须在未来五分钟内。",
  policy_denied: "此动作不在当前低风险模拟执行策略内。",
  unauthorized: "凭证无效或未配置，请检查服务端 API 凭证。",
  forbidden: "当前凭证没有此操作的权限。",
  database_not_ready: "数据库尚未就绪，请确认连接并执行最新迁移。",
  invalid_request:
    "输入未通过校验。请检查必填项、能力类型、版本格式与 Schema。",
  resource_conflict: "记录已存在：请检查型号版本或设备标识是否重复。",
  revision_conflict: "设备已被其他请求更新。请重新加载详情后再修改。",
  resource_not_found: "记录不存在或不在当前授权域内。",
};

export class RegistryError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function registryRequest<T>(
  token: string,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...init.headers,
      },
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
  } catch {
    throw new Error("连接失败，请检查服务并重试。");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const message = messages[body.code] ?? `请求失败（${response.status}）`;
    throw new RegistryError(message, response.status);
  }
  return response.json() as Promise<T>;
}

export const virtualLightCapabilities: Capabilities = {
  properties: {
    power: { schema: { type: "boolean" }, readable: true, writable: false },
    brightness: {
      schema: { type: "integer", minimum: 0, maximum: 100 },
      readable: true,
      writable: false,
      unit: "%",
    },
  },
  actions: {
    set_power: {
      input_schema: {
        type: "object",
        properties: { value: { type: "boolean" } },
        required: ["value"],
        additionalProperties: false,
      },
      risk: "low",
      confirmation: "observed_state",
      offline_policy: "cached_authorization",
    },
    set_brightness: {
      input_schema: {
        type: "object",
        properties: { value: { type: "integer", minimum: 0, maximum: 100 } },
        required: ["value"],
        additionalProperties: false,
      },
      risk: "low",
      confirmation: "observed_state",
      offline_policy: "cached_authorization",
    },
  },
  events: { power_changed: { data_schema: { type: "boolean" } } },
};
