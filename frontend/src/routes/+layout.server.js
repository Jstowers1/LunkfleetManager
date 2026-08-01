import { env } from '$env/dynamic/private';
import { timingSafeEqual } from 'crypto';

//Tokens from env only — no hardcoded fallbacks in frontend source.
//If these aren't set, nobody can log in (correct secure default).
//Set them in frontend/.env to match the backend tokens.
const ADMIN_TOKEN = env.ADMIN_TOKEN || '';
const GUEST_TOKEN = env.GUEST_TOKEN || '';

//Constant-time comparison so token guessing can't time-leak.
function safeCompare(a, b) {
    if (!a || !b) return false;
    const bufA = Buffer.from(a);
    const bufB = Buffer.from(b);
    if (bufA.length !== bufB.length) return false;
    return timingSafeEqual(bufA, bufB);
}

export function load({ url, cookies }) {
    //1. Magic link: ?token=friend_pass bakes a 1-year cookie.
    const tokenParam = url.searchParams.get('token');

    if (tokenParam) {
        cookies.set('auth_token', tokenParam, {
            path: '/',
            maxAge: 60 * 60 * 24 * 365,
            //ponytail: secure=false because the dashboard is also accessed
            //over plain HTTP on the LAN. Set to true when HTTPS is the only
            //access path.
            secure: false,
            httpOnly: false,
            sameSite: 'lax'
        });
    }

    //2. Resolve role from token.
    const currentToken = tokenParam || cookies.get('auth_token');
    let userRole = 'unauthorized';

    if (safeCompare(currentToken, ADMIN_TOKEN)) userRole = 'admin';
    else if (safeCompare(currentToken, GUEST_TOKEN)) userRole = 'guest';

    return {
        role: userRole,
        token: currentToken
    };
}
