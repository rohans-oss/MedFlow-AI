import { NextResponse, type NextRequest } from "next/server";

// Cheap route guard: the real authorisation happens in the API on every request.
// `mf_session` is a non-sensitive hint cookie set alongside the HttpOnly auth cookies.
export function proxy(request: NextRequest) {
  const hasSession = request.cookies.has("mf_session");
  const { pathname } = request.nextUrl;
  const isLogin = pathname === "/login";

  if (!hasSession && !isLogin) {
    const url = new URL("/login", request.url);
    if (pathname !== "/") url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }
  if (hasSession && (isLogin || pathname === "/")) {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|svg|ico|jpg|webp)$).*)"],
};
