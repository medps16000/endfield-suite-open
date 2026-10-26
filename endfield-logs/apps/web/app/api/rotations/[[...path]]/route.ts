// Local/standalone preview only. Production Nginx serves /api via FastAPI.
import { ROTATIONS_ENABLED } from "../../../../lib/feature-flags";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path?: string[] }> };

async function proxy(request: Request, context: Context) {
  if (!ROTATIONS_ENABLED) {
    return Response.json({ error: { code: "ROTATIONS_DISABLED", message: "排轴功能暂时停用。" } }, {
      status: 410, headers: { "Cache-Control": "no-store" },
    });
  }
  const { path = [] } = await context.params;
  if (path.length > 1 || (path.length === 1 && !/^[a-zA-Z0-9_-]{1,128}$/.test(path[0]))) {
    return Response.json({ error: { message: "未找到排轴接口。" } }, { status: 404 });
  }
  const base = (process.env.ROTATIONS_API_BASE_URL_INTERNAL || process.env.API_BASE_URL_INTERNAL || "http://127.0.0.1:8000").trim().replace(/\/+$/, "");
  const headers = new Headers();
  for (const name of ["cookie", "authorization", "content-type", "origin", "x-endfield-rotation-account"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const query = new URL(request.url).search;
  try {
    const response = await fetch(`${base}/api/rotations${path.length ? `/${path[0]}` : ""}${query}`, {
      method: request.method, headers, cache: "no-store", signal: request.signal,
      ...(request.method !== "GET" ? { body: await request.text() } : {}),
    });
    return new Response(response.body, { status: response.status, headers: {
      "Content-Type": response.headers.get("Content-Type") || "application/json", "Cache-Control": "no-store",
    } });
  } catch {
    return Response.json({ error: { message: "排轴服务暂时不可用，请稍后重试。" } }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
