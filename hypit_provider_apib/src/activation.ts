import {
  createRuntimeEndpointAdapterFacet,
  runtimeConfigCredentialRef,
  runtimeConfigExact,
  runtimeConfigObject,
  runtimeConfigPositiveInteger,
  runtimeConfigString,
} from "@hypit/hypit/runtime-kit";

import { createApibProvider, providerModule } from "./provider.js";

export default {
  format: "hypit.node-package@1" as const,
  hostFacets: [
    createRuntimeEndpointAdapterFacet({
      use: providerModule.name,
      activate(context) {
        const config = runtimeConfigObject(context.config, "APIB service");
        runtimeConfigExact(
          config,
          ["baseUrl", "apiKey", "concurrency", "pollIntervalMs"],
          "APIB service",
        );
        const baseUrl = runtimeConfigString(config.baseUrl, "APIB baseUrl");
        const apiKey = runtimeConfigCredentialRef(config.apiKey, "APIB apiKey");
        if (!baseUrl || !apiKey || !context.pool) {
          throw new Error("APIB requires baseUrl, apiKey and a provider pool");
        }
        return {
          endpoint: createApibProvider({
            instance: context.instance,
            pool: context.pool,
            baseUrl,
            apiKey,
            concurrency:
              runtimeConfigPositiveInteger(config.concurrency, "concurrency") ?? 2,
            pollIntervalMs:
              runtimeConfigPositiveInteger(config.pollIntervalMs, "pollIntervalMs") ??
              5_000,
          }),
        };
      },
    }),
  ],
};
