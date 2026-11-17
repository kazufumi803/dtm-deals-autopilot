import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

export default defineConfig({
  site: process.env.SITE_URL || 'https://dtm-deals.example.com',
  integrations: [
    tailwind({
      applyBaseStyles: false,
    }),
  ],
});
