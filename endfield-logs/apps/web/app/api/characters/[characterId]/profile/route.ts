// Local/standalone preview proxy. Production Nginx routes /api directly to API.
export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ characterId: string }> }) {
  const { characterId } = await context.params;
  if (!/^chr_[0-9]{4}_[a-z0-9]+$/.test(characterId)) {
    return Response.json({ error: { message: "未找到对应角色。" } }, { status: 404 });
  }
  const base = (process.env.CHARACTER_PROFILE_API_BASE_URL_INTERNAL || process.env.API_BASE_URL_INTERNAL || "http://127.0.0.1:8000").trim().replace(/\/+$/, "");
  const query = new URL(request.url).searchParams;
  try {
    const response = await fetch(`${base}/api/characters/${characterId}/profile?${query}`, { cache: "no-store", signal: request.signal });
    return new Response(response.body, { status: response.status, headers: {
      "Content-Type": response.headers.get("Content-Type") || "application/json",
      "Cache-Control": "no-store",
    } });
  } catch {
    return Response.json({ error: { message: "角色统计暂时不可用，请稍后重试。" } }, { status: 503 });
  }
}
