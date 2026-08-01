/**
 * Redirect legacy /calendar/ to the new Jin10 data page under futures compass.
 */
export const onRequest = () => new Response(null, {
  status: 301,
  headers: {
    location: '/futures-compass/jin10/',
    'cache-control': 'public, max-age=3600',
  },
});