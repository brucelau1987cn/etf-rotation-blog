import { getCollection } from 'astro:content';
import rss from '@astrojs/rss';
import { SITE_DESCRIPTION, SITE_TITLE } from '../consts';
import { isPublicEtfPost, sortByPubDateDesc } from '../lib/posts';

export async function GET(context) {
	const posts = (await getCollection('blog')).filter(isPublicEtfPost).sort(sortByPubDateDesc);
	return rss({
		title: SITE_TITLE,
		description: SITE_DESCRIPTION,
		site: context.site,
		items: posts.map((post) => ({
			...post.data,
			link: `/blog/${post.id}/`,
		})),
	});
}
