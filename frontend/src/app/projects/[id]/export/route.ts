import { NextResponse } from "next/server";
import { ApiError, api } from "@/lib/api";
import { readToken } from "@/lib/session";

/**
 * Hand the Excel deliverable to the browser (spec §34).
 *
 * A route handler rather than a link straight to the API: the session token is
 * httpOnly and only the server can read it, so the bytes are fetched here and
 * passed on. The browser never holds a credential, and the download stays a
 * plain link.
 *
 * `acknowledge_unreconciled` is carried through rather than defaulted: §19
 * requires taking failing output away to be a deliberate act.
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  if ((await readToken()) === null) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const { id } = await params;
  const acknowledge =
    new URL(request.url).searchParams.get("acknowledge_unreconciled") === "true";

  try {
    const file = await api.exportExcel(id, acknowledge);
    return new Response(file.bytes, {
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": `attachment; filename="${file.filename}"`,
        ...(file.unreconciled ? { "X-IFRS18-Reconciliation": "FAILED" } : {}),
      },
    });
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json(error.body ?? { detail: error.message }, {
        status: error.status,
      });
    }
    throw error;
  }
}
