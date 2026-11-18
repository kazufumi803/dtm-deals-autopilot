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

function extractPlainSummary(markdown: string, maxLength = 220): string {
  const withoutHeadings = markdown
    .replace(/^#+\s+.*$/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^[-\s*]+/gm, '')
    .replace(/\s+/g, ' ')
    .trim();

  if (withoutHeadings.length <= maxLength) {
    return withoutHeadings;
  }
  return withoutHeadings.slice(0, maxLength - 1).trimEnd() + '…';
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

  const lastBuildDate =
    sortedDeals.length > 0
      ? sortedDeals[0].data.pubDate.toUTCString()
      : new Date().toUTCString();

  const itemsXml = sortedDeals
    .map((deal) => {
      const articleUrl = `${baseUrl}/deals/${deal.slug}`;
      const summary = extractPlainSummary(deal.body);
      const hashtags =
        deal.data.category === 'Free'
          ? '#DTM #無料プラグイン #プラグインセール'
          : '#DTM #プラグインセール';
      const descriptionWithTags = `${summary} ${hashtags}`.trim();

      return [
        '    <item>',
        `      <title>${escapeXml(deal.data.title)}</title>`,
        `      <link>${escapeXml(articleUrl)}</link>`,
        `      <guid isPermaLink="true">${escapeXml(articleUrl)}</guid>`,
        `      <pubDate>${deal.data.pubDate.toUTCString()}</pubDate>`,
        `      <category>${escapeXml(deal.data.category)}</category>`,
        `      <description>${escapeXml(descriptionWithTags)}</description>`,
        '    </item>',
      ].join('\n');
    })
    .join('\n');

  const rssXml = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
    '  <channel>',
    '    <title>SYNTH &amp; PLUGIN DEAL RADAR — 最新DTMプラグインセール＆無料配布情報</title>',
    `    <link>${escapeXml(baseUrl)}</link>`,
    '    <description>海外のDTMプラグイン・ソフト音源の最新セール情報・期間限定無料配布（Freebie）情報をAIで最速解説するDTMer向け特化メディア</description>',
    '    <language>ja</language>',
    `    <lastBuildDate>${lastBuildDate}</lastBuildDate>`,
    `    <atom:link href="${escapeXml(`${baseUrl}/rss.xml`)}" rel="self" type="application/rss+xml" />`,
    itemsXml,
    '  </channel>',
    '</rss>',
    '',
  ].join('\n');

  return new Response(rssXml, {
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
    },
  });
};
