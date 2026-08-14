tailwind.config = {
    darkMode: 'class',
    theme: {
        extend: {
            colors: {
                vintage: {
                    base: '#0f0f0f',       /* Obsidian Black */
                    card: '#171717',       /* Charcoal Card */
                    border: '#262626',     /* Soft Muted Border */
                    sand: '#d4c5b9',       /* Classic Vintage Sand */
                    cream: '#f5f2eb',      /* Off-White Cream */
                    accent: '#a39281',     /* Warm Muted Taupe */
                    hover: '#332f2c'
                }
            },
            fontFamily: {
                serif: ['Georgia', 'Cambria', 'Times New Roman', 'serif'],
                sans: ['system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif']
            }
        }
    }
}