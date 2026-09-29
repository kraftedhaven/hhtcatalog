import { createClient } from "@supabase/supabase-js";

// Supabase project URLs and publishable keys are browser-safe identifiers.
// Heroku's Docker source builds do not expose config vars to Vite at build time,
// so retain the production values as fallbacks while allowing local overrides.
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || "https://rjdhmnacbqelgekpodhb.supabase.co";
const supabasePublishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY || "sb_publishable_i6nzq9faeNMr5WCjKt7l8A_2iAM3JAo";

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
