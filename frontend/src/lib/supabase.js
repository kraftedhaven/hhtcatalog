import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabasePublishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

export const isSupabaseConfigured = Boolean(supabaseUrl && supabasePublishableKey);

export const supabase = isSupabaseConfigured
    ? createClient(supabaseUrl, supabasePublishableKey)
    : null;

export async function getAccessToken() {
    if (!supabase) return null;

    const {
        data: { session },
    } = await supabase.auth.getSession();

    return session?.access_token || null;
}
