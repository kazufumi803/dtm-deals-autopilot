import { defineCollection, z } from 'astro:content';

const dealsCollection = defineCollection({
  type: 'content',
  schema: z.object({
    title: z.string(),
    pubDate: z.coerce.date(),
    category: z.enum(['Free', 'Sale']),
    affiliateUrl: z.string().url(),
  }),
});

export const collections = {
  deals: dealsCollection,
};
