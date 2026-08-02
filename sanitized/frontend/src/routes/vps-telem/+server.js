import { json } from '@sveltejs/kit';

//This endpoint proxies to the backend which handles the vnstat call
export async function GET() {
    try {
        const res = await fetch('http://127.0.0.1:8000/api/vps-telem');
        if (!res.ok) {
            throw new Error(`Backend error: ${res.status}`);
        }
        const data = await res.json();
        return json(data);
    } catch (e) {
        console.error("vps-telem proxy error:", e);
        return json({
            bandwidth: {
                percent: 0,
                text: '0 GB / 3000 GB',
                reset_date: 'Resets Unknown',
                used: '0 GB'
            },
            per_server_bandwidth: {}
        });
    }
}
