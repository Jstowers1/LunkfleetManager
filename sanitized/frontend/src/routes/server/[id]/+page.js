import { browser } from '$app/environment';

//Add parent to the function arguments
export async function load({ fetch, params, parent }) {
    
    //Grab the token exported from the layout
    const { token } = await parent(); 

    const BACKEND_URL = browser 
        ? window.location.origin 
        : 'http://127.0.0.1';
    
    //Manually construct the Cookie header if a token exists
    const headers = token ? { Cookie: `auth_token=${token}` } : {};
    
    //Inject the headers into the fetch request
    const response = await fetch(`${BACKEND_URL}/api/servers/${params.id}`, {
        headers: headers
    });
    
    if (!response.ok) {
        return { 
            server: { 
                id: params.id,       
                name: params.id,     
                status: "offline", 
                port: "Unknown",
                game_version: "unknown",
                ram_allocated: 0,
                ram_used: 0,
                stats: { status: "offline" } 
            } 
        };
    }
    
    const data = await response.json();
    return { server: data };
}
