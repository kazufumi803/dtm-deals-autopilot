import type { APIRoute } from 'astro';

export const GET: APIRoute = ({ site }) => {
  const baseUrl = (
    site?.toString() ||
    import.meta.env.SITE_URL ||
    'https://dtm-deals-autopilot.pages.dev'
  ).replace(/\/$/, '');

  const body = [
    'User-agent: *',
    'Allow: /',
    '',
    `Sitemap: ${baseUrl}/sitemap.xml`,
    '',
  ].join('\n');

  return new Response(body, {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
    },
  });
};
