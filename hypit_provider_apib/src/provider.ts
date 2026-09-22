import {
  canonicalize,
  credentialRef,
  defineEndpointPackage,
  wakeAfter,
} from "@hypit/hypit/endpoint-kit";
import type {
  AsyncEndpoint,
  BlobRef,
  CapabilityRef,
  CredentialRef,
  EndpointCredential,
  EndpointInvocationContext,
  EndpointRequest,
} from "@hypit/hypit/endpoint-kit";
import {
  compileWireRequest,
  generationTypes,
  mappingSupportsRequest,
  sealGeneratedImageSet,
  sealGeneratedVideoSet,
  selectWireModelForRequest,
} from "@hypit/hypit/generation";
import type {
  GenerationRequest,
  GenerationWireMapping,
} from "@hypit/hypit/generation";

export const providerModule = {
  name: "@zhangxiaoxing/provider-apib",
  version: "1",
} as const;

type Handle = {
  contract: "zhangxiaoxing.apib-task@1";
  taskId: string;
  route: string;
  kind: "image" | "video";
  urls?: string[];
};

type PreparedRoute = {
  mapping: GenerationWireMapping;
  key: string;
  kind: "image" | "video";
  model: string;
  payload: Record<string, unknown>;
  returns: unknown;
};

const GPT_IMAGE = { name: "@hypit/gpt-image", version: "1" } as const;
const SEEDREAM = { name: "@hypit/seedream", version: "1" } as const;
const NANO_BANANA = { name: "@hypit/nano-banana", version: "1" } as const;
const SEEDANCE = { name: "@hypit/seedance", version: "1" } as const;

const imageFields = {
  prompt: { as: "value", field: "prompt" },
  aspectRatio: { as: "value", field: "size" },
  resolution: { as: "value", field: "resolution" },
  background: { as: "value", field: "background" },
  images: { as: "urlArray", field: "image_urls" },
} as const satisfies GenerationWireMapping["fields"];

const seedanceFields = {
  prompt: { as: "value", field: "prompt" },
  aspectRatio: { as: "value", field: "size" },
  resolution: { as: "value", field: "resolution" },
  duration: { as: "value", field: "duration" },
  generateAudio: { as: "value", field: "generate_audio" },
  webSearch: { as: "value", field: "web_search" },
  referenceImage: { as: "urlArray", field: "image_urls" },
} as const satisfies GenerationWireMapping["fields"];

const imageMappings: readonly GenerationWireMapping[] = [
  {
    capability: { module: GPT_IMAGE, name: "gpt-image-2" },
    result: "image",
    routes: [{ model: "gpt-image-2" }],
    fields: imageFields,
  },
  {
    capability: { module: SEEDREAM, name: "seedream-5-lite" },
    result: "image",
    routes: [{ model: "seedream-5.0-lite" }],
    fields: imageFields,
  },
  {
    capability: { module: NANO_BANANA, name: "nano-banana-2" },
    result: "image",
    routes: [{ model: "gemini-3.1-flash-image-preview" }],
    fields: imageFields,
  },
  {
    capability: { module: NANO_BANANA, name: "nano-banana-pro" },
    result: "image",
    routes: [{ model: "gemini-3-pro-image-preview" }],
    fields: imageFields,
  },
];

const videoMappings: readonly GenerationWireMapping[] = [
  {
    capability: { module: SEEDANCE, name: "seedance-2" },
    result: "video",
    routes: [{ model: "seedance-2.0" }],
    fields: seedanceFields,
  },
  {
    capability: { module: SEEDANCE, name: "seedance-2-fast" },
    result: "video",
    routes: [{ model: "seedance-2.0-fast" }],
    fields: seedanceFields,
  },
  {
    capability: { module: SEEDANCE, name: "seedance-2-mini" },
    result: "video",
    routes: [{ model: "seedance-2.0-mini" }],
    fields: seedanceFields,
  },
  {
    capability: { module: SEEDANCE, name: "seedance-2.5" },
    result: "video",
    routes: [{ model: "seedance-2.5" }],
    fields: seedanceFields,
  },
];

const mappings = [...imageMappings, ...videoMappings];

function object(value: unknown, subject: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${subject} must be an object`);
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, subject: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${subject} must be nonempty text`);
  }
  return value;
}

function apiAddress(value: string): string {
  const url = new URL(value.trim());
  if (
    url.protocol !== "https:" &&
    !(url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname))
  ) {
    throw new Error("APIB baseUrl requires HTTPS or loopback HTTP");
  }
  return url.href.replace(/\/+$/u, "");
}

function apiKey(
  credentials: Readonly<Record<string, EndpointCredential>>,
): string {
  return text(credentials.apiKey?.secret, "APIB apiKey");
}

function capabilityKey(capability: CapabilityRef): string {
  return `${capability.module.name}@${capability.module.version}#${capability.name}`;
}

function routeFor(capability: CapabilityRef): GenerationWireMapping {
  const key = capabilityKey(capability);
  const route = mappings.find(
    (mapping) => capabilityKey(mapping.capability) === key,
  );
  if (!route) throw new Error(`APIB does not implement capability ${key}`);
  return route;
}

function supportFor(mapping: GenerationWireMapping) {
  return (request: EndpointRequest) => {
  return mappingSupportsRequest(
    mapping,
    request.constraints,
  )
    ? { status: "supported" as const }
    : {
        status: "unsupported" as const,
        reason: "APIB does not accept one of the requested inputs",
      };
  };
}

function failure(
  value: unknown,
): { code: string; message: string } | undefined {
  const root = object(value, "APIB response");
  const error = root.error;
  if (error === null || typeof error !== "object" || Array.isArray(error)) {
    return undefined;
  }
  const detail = error as Record<string, unknown>;
  const message =
    typeof detail.message === "string" ? detail.message : undefined;
  if (!message) return undefined;
  return {
    code:
      typeof detail.code === "string"
        ? detail.code
        : typeof detail.type === "string"
          ? detail.type
          : "APIB_ERROR",
    message: message.replace(/https?:\/\/\S+/giu, "[redacted-url]"),
  };
}

class ApibClient {
  constructor(
    readonly baseUrl: string,
    readonly fetcher: typeof globalThis.fetch = globalThis.fetch,
  ) {}

  async json(
    path: string,
    key: string,
    init: RequestInit = {},
  ): Promise<Record<string, unknown>> {
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        authorization: `Bearer ${key}`,
        ...(init.headers ?? {}),
      },
      signal: AbortSignal.timeout(120_000),
    });
    const body = await response.json();
    if (!response.ok) {
      const error = failure(body);
      throw Object.assign(
        new Error(
          `APIB ${init.method ?? "GET"} ${path} returned HTTP ${response.status}` +
            (error ? `; ${error.code}: ${error.message}` : ""),
        ),
        error ? { code: error.code } : {},
      );
    }
    return object(body, "APIB response");
  }

  async download(
    url: string,
    expected: "image" | "video",
  ): Promise<{ bytes: Uint8Array; mediaType: string }> {
    const response = await this.fetcher(url, {
      signal: AbortSignal.timeout(600_000),
    });
    if (!response.ok) {
      throw new Error(`APIB asset returned HTTP ${response.status}`);
    }
    const contentType = response.headers
      .get("content-type")
      ?.split(";", 1)[0]
      ?.trim();
    return {
      bytes: new Uint8Array(await response.arrayBuffer()),
      mediaType:
        contentType?.startsWith(`${expected}/`) === true
          ? contentType
          : expected === "image"
            ? "image/png"
            : "video/mp4",
    };
  }
}

function resolver(
  client: ApibClient,
  context: EndpointInvocationContext,
): (
  artifact: BlobRef,
  fields?: Readonly<Record<string, string | number | boolean>>,
) => Promise<string> {
  const resolved = new Map<string, Promise<string>>();
  return (artifact) => {
    const existing = resolved.get(artifact.resource);
    if (existing) return existing;
    const promise = (async () => {
      const bytes = await context.resources.get(artifact.resource);
      if (!bytes) throw new Error(`Reference ${artifact.resource} is unavailable`);
      const form = new FormData();
      const extension = artifact.mediaType.split("/", 2)[1] ?? "bin";
      form.append(
        "file",
        new Blob([new Uint8Array(bytes)], { type: artifact.mediaType }),
        `reference.${extension}`,
      );
      const response = await client.fetcher(`${client.baseUrl}/uploads/images`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${apiKey(context.credentials)}`,
        },
        body: form,
        signal: AbortSignal.timeout(180_000),
      });
      const body = await response.json();
      if (!response.ok) {
        const error = failure(body);
        throw new Error(
          `APIB image upload returned HTTP ${response.status}` +
            (error ? `; ${error.code}: ${error.message}` : ""),
        );
      }
      return text(object(body, "APIB upload").url, "APIB upload URL");
    })();
    resolved.set(artifact.resource, promise);
    return promise;
  };
}

function prepare(
  mapping: GenerationWireMapping,
  constraints: unknown,
): {
  kind: "image" | "video";
  model: string;
  compile: (
    resolve: ReturnType<typeof resolver>,
  ) => Promise<Record<string, unknown>>;
} {
  const request = constraints as GenerationRequest;
  const model = selectWireModelForRequest(mapping, request);
  return {
    kind: mapping.result === "video" ? "video" : "image",
    model,
    compile: async (resolve) => {
      const compiled = object(
        await compileWireRequest(mapping, request, resolve),
        "APIB compiled request",
      );
      const input = object(
        compiled.input ?? compiled,
        "APIB compiled request input",
      );
      const payload = {
        model:
          typeof compiled.model === "string" ? compiled.model : model,
        ...input,
      };
      payload.n = 1;
      payload.nsfw_check = true;
      if (payload.web_search === true) {
        payload.tools = [{ type: "web_search" }];
      }
      delete payload.web_search;
      return payload;
    },
  };
}

function resultUrls(
  task: Record<string, unknown>,
  expected: "image" | "video",
): string[] {
  const result = object(task.result, "APIB task result");
  const items = result[`${expected}s`];
  if (!Array.isArray(items)) return [];
  const urls: string[] = [];
  for (const item of items) {
    const value = object(item, "APIB result item").url;
    if (typeof value === "string") urls.push(value);
    else if (Array.isArray(value)) {
      urls.push(
        ...value.filter((url): url is string => typeof url === "string"),
      );
    }
  }
  return urls.filter((url) => /^https?:\/\//u.test(url));
}

function endpoint(
  client: ApibClient,
  pollIntervalMs: number,
): AsyncEndpoint {
  return {
    async start(context) {
      const mapping = routeFor(context.need.capability);
      const prepared = prepare(mapping, context.need.constraints);
      await context.reportProgress?.({
        phase: `Preparing APIB request: ${prepared.model}`,
      });
      const payload = await prepared.compile(
        resolver(client, context),
      );
      const path =
        prepared.kind === "image"
          ? "/images/generations"
          : "/videos/generations";
      const response = await client.json(
        path,
        apiKey(context.credentials),
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      const data = response.data;
      if (!Array.isArray(data) || data.length === 0) {
        throw new Error("APIB response has no task data");
      }
      const taskId = text(
        object(data[0], "APIB task").task_id,
        "APIB task_id",
      );
      const handle: Handle = {
        contract: "zhangxiaoxing.apib-task@1",
        taskId,
        route: capabilityKey(context.need.capability),
        kind: prepared.kind,
      };
      const receipt = { id: taskId };
      await context.checkpoint?.({
        handle: canonicalize(handle),
        receipt,
      });
      return {
        ...wakeAfter(handle, pollIntervalMs, Date.now(), {
          phase: "submitted",
        }),
        receipt,
      };
    },
    async poll(context) {
      const handle = object(context.handle, "APIB handle") as Handle;
      const response = await client.json(
        `/tasks/${encodeURIComponent(handle.taskId)}`,
        apiKey(context.credentials),
      );
      const data = object(response.data, "APIB task");
      const status = String(data.status ?? "").toLowerCase();
      if (["submitted", "processing", "pending"].includes(status)) {
        return wakeAfter(handle, pollIntervalMs, Date.now(), {
          phase: status,
        });
      }
      if (["failed", "cancelled"].includes(status)) {
        const error = failure(data);
        return {
          status: "failed",
          receipt: { id: handle.taskId },
          failure: {
            code: error?.code ?? "APIB_TASK_FAILED",
            message:
              error?.message ??
              `APIB task ${handle.taskId} failed without a public error`,
          },
        };
      }
      if (status !== "completed") {
        throw new Error(`APIB returned unknown task status ${status}`);
      }
      const urls = resultUrls(data, handle.kind);
      if (urls.length === 0) {
        throw new Error("APIB task completed without a result URL");
      }
      return {
        status: "ready",
        handle: canonicalize({ ...handle, urls }),
        receipt: { id: handle.taskId },
      };
    },
    async collect(context) {
      const handle = object(context.handle, "APIB handle") as Handle;
      if (!Array.isArray(handle.urls)) {
        throw new Error("APIB handle has no result URLs");
      }
      const blobs: BlobRef[] = [];
      for (const url of handle.urls) {
        const asset = await client.download(url, handle.kind);
        blobs.push(
          await context.resources.put(asset.bytes, asset.mediaType),
        );
      }
      const value =
        handle.kind === "image"
          ? sealGeneratedImageSet({ images: blobs })
          : sealGeneratedVideoSet({ videos: blobs });
      return {
        status: "completed",
        result: {
          value: {
            kind: "inline",
            value: canonicalize(value),
          },
        },
        receipt: { id: handle.taskId },
      };
    },
  };
}

export function createApibProvider(options: {
  instance?: string;
  pool?: string;
  baseUrl?: string;
  apiKey?: CredentialRef;
  concurrency?: number;
  pollIntervalMs?: number;
  fetch?: typeof globalThis.fetch;
}) {
  const client = new ApibClient(
    apiAddress(options.baseUrl ?? "https://api.apib.ai/v1"),
    options.fetch ?? globalThis.fetch,
  );
  const capabilityEndpoint = endpoint(
    client,
    options.pollIntervalMs ?? 5_000,
  );
  return defineEndpointPackage({
    module: providerModule,
    facet: "gateway",
    instance: options.instance ?? "apib.default",
    pool: options.pool ?? options.instance ?? "apib.default",
    pricing: { kind: "page", url: "https://docs.apib.ai/cn" },
    credentials: {
      apiKey: options.apiKey ?? credentialRef("platform", "apib.api-key"),
    },
    credentialInputs: {
      apiKey: { label: "APIB API key" },
    },
    defaultConcurrency: options.concurrency ?? 2,
    capabilities: mappings.map((mapping) => ({
      capability: mapping.capability,
      returns:
        mapping.result === "image"
          ? generationTypes.imageSet
          : generationTypes.videoSet,
      lifecycle: "asynchronous" as const,
      endpoint: capabilityEndpoint,
      capacity: mapping.capability.name,
      supports: supportFor(mapping),
    })),
  });
}
