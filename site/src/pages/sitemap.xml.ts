import type { APIRoute } from 'astro';
import { getCollection } from 'astro:content';

function escapeXml(unsafe: string): string {
  return unsafe
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

export const GET: APIRoute = async ({ site }) => {
  const baseUrl = (
    site?.toString() ||
    import.meta.env.SITE_URL ||
    'https://dtm-deals-autopilot.pages.dev'
  ).replace(/\/$/, '');

  const deals = await getCollection('deals');
  const sortedDeals = deals.sort(
    (a, b) => b.data.pubDate.valueOf() - a.data.pubDate.valueOf()
  );

  const latestDateIso =
    sortedDeals.length > 0
      ? sortedDeals[0].data.pubDate.toISOString()
      : new Date().toISOString();

  const urls: Array<{
    loc: string;
    lastmod: string;
    changefreq: string;
    priority: string;
  }> = [
    {
      loc: `${baseUrl}/`,
      lastmod: latestDateIso,
      changefreq: 'daily',
      priority: '1.0',
    },
    ...sortedDeals.map((deal) => ({
      loc: `${baseUrl}/deals/${deal.slug}`,
      lastmod: deal.data.pubDate.toISOString(),
      changefreq: 'weekly',
      priority: '0.8',
    })),
  ];

  const urlEntriesXml = urls
    .map((u) =>
      [
        '  <url>',
        `    <loc>${escapeXml(u.loc)}</loc>`,
        `    <lastmod>${escapeXml(u.lastmod)}</lastmod>`,
        `    <changefreq>${u.changefreq}</changefreq>`,
        `    <priority>${u.priority}</priority>`,
        '  </url>',
      ].join('\n')
    )
    .join('\n');

  const xml = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    urlEntriesXml,
    '</urlset>',
    '',
  ].join('\n');

  return new Response(xml, {
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
    },
  });
};
