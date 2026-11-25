import { createAuthClient } from "@neondatabase/auth";

const apiBase = (import.meta.env.DEV
    ? import.meta.env.VITE_PUBLIC_API_URL || import.meta.env.VITE_API_BASE_URL || ""
    : "").replace(/\/+$/, "");

let authClient;

export async function loadAuthClient() {
    const response = await fetch(`${apiBase}/api/auth/config`);
    if (!response.ok) throw new Error("Could not load authentication settings.");
    const { authUrl } = await response.json();
    if (!authUrl) throw new Error("Neon Auth is not configured.");
    authClient = createAuthClient(authUrl, { fetchOptions: { credentials: "include" } });
    return authClient;
}

export async function getAccessToken() {
    const { data, error } = await authClient?.getSession() || {};
    if (error) throw new Error(error.message || "Could not refresh your session.");
    return data?.session?.token || null;
}

export async function checkSellerAccess() {
    const token = await getAccessToken();
    if (!token) throw new Error("Your session has expired. Please sign in again.");
    const response = await fetch(`${apiBase}/api/auth/seller`, {
        headers: { Authorization: `Bearer ${token}` },
    });
    if (response.status === 403) return false;
    if (!response.ok) throw new Error("Could not verify seller access.");
    return true;
}
