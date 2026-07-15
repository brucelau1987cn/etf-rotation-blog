import { json, requireUser } from '../../_lib/auth.js';

export async function onRequestGet({ request, env }) {
  const result = await requireUser(request, env);
  if (result.error) return result.error;
  const { user } = result;
  return json({ user: { username: user.username, role: user.role, must_change_password: Boolean(user.must_change_password) } });
}
