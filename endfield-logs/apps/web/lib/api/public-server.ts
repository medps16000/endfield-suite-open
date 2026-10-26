import "server-only";

const INTERNAL_API_BASE_URL =
  process.env.API_BASE_URL_INTERNAL?.trim().replace(/\/+$/, "") ||
  process.env.NEXT_PUBLIC_API_BASE_URL?.trim().replace(/\/+$/, "") ||
  "http://127.0.0.1:8000";

export async function fetchPublicApiServer<T>(path: string, init?: RequestInit, baseUrl = INTERNAL_API_BASE_URL): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, init);

  if (!response.ok) {
    let message = `接口请求失败：${response.status}`;
    try {
      const data = (await response.json()) as { error?: { message?: string } };
      message = data.error?.message ?? message;
    } catch {
      // Ignore JSON parse errors and keep the generic message.
    }
    throw new Error(message);
  }

  return (await response.json()) as T;
}
