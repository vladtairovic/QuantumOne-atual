import type { NextConfig } from "next";

function seconds(value: string | undefined, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

// A análise unificada (POST /analise) percorre a fila de LLMs em sequência e pode
// levar minutos; o proxy precisa esperar mais que o maior tempo limite da API.
const PROXY_TIMEOUT_SECONDS = Math.max(
  seconds(process.env.OPTIMIZATION_TIMEOUT_SECONDS, 60),
  seconds(process.env.API_TIMEOUT_SECONDS, 60),
  seconds(process.env.ANALISE_TIMEOUT_SECONDS, 900),
) + 15;

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: (process.env.API_INTERNAL_URL || "http://api:8000") + "/:path*" }];
  },
  experimental: {
    proxyTimeout: PROXY_TIMEOUT_SECONDS * 1000,
  },
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        { key: "Content-Security-Policy", value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" },
      ],
    }];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
