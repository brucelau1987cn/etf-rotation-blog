import React, { useEffect, useState } from 'react';
import { Button, Card, Input, Modal, Tag } from '@douyinfe/semi-ui';

const api = async (url, options = {}) => {
  const response = await fetch(url, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  let data = {};
  try { data = await response.json(); } catch {}
  return { status: response.status, data };
};

const guessCode = (name) => ({
  碳酸锂: 'LC', 多晶硅: 'PS', 工业硅: 'SI', 黄金: 'AU', 白银: 'AG',
  沪铜: 'CU', 铜: 'CU', 沪铝: 'AL', 铝: 'AL', 原油: 'SC',
  生猪: 'LH', 猪肉: 'LH', 焦煤: 'JM', 焦炭: 'J',
}[name] || '');

export default function SemiFuturesAdmin() {
  const [authorized, setAuthorized] = useState(null);
  const [items, setItems] = useState([]);
  const [name, setName] = useState('');
  const [exchange, setExchange] = useState('');
  const [code, setCode] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const me = await api('/api/admin/me');
    if (me.status !== 200 || !me.data?.ok) {
      setAuthorized(false);
      return;
    }
    setAuthorized(true);
    const result = await api('/api/admin/futures-watchlist');
    if (result.status === 200 && result.data?.ok) setItems(result.data.items || []);
    else setMessage(result.data?.error || '加载标的失败');
  };

  useEffect(() => { load(); }, []);

  const mutate = async (url, body, successText) => {
    setBusy(true);
    const result = await api(url, { method: 'POST', body: JSON.stringify(body) });
    setBusy(false);
    if (result.status === 200 && result.data?.ok) {
      setItems(result.data.items || []);
      setMessage(successText);
      return true;
    }
    setMessage(result.data?.error || '操作失败');
    return false;
  };

  const add = async (event) => {
    event.preventDefault();
    const cleanName = name.trim();
    const cleanCode = (code.trim().toUpperCase() || guessCode(cleanName));
    if (!cleanName || !cleanCode) {
      setMessage('名称必填，代码无法推断时请手工填写');
      return;
    }
    const continuous = `${cleanCode}0`;
    const ok = await mutate('/api/admin/futures-watchlist', {
      code: cleanCode,
      continuous,
      name: cleanName,
      exchange: exchange.trim() || '未知交易所',
      unit: '元/吨', tick: 1, edge_symbol: `nf_${continuous}`, sort_order: 999, enabled: 1,
    }, `已添加 ${cleanName}`);
    if (ok) { setName(''); setExchange(''); setCode(''); }
  };

  const toggle = (item) => mutate(
    '/api/admin/futures-watchlist?action=toggle',
    { code: item.code, enabled: item.enabled ? 0 : 1 },
    `${item.name || item.code} 已${item.enabled ? '停用' : '启用'}`,
  );

  const remove = (item) => Modal.confirm({
    title: '确认删除标的？',
    content: `删除「${item.name || item.code}」后会从名单移除。`,
    okText: '删除', cancelText: '取消', okType: 'danger',
    onOk: () => mutate(
      '/api/admin/futures-watchlist?action=delete', { code: item.code }, `已删除 ${item.name || item.code}`,
    ),
  });

  if (authorized === null) return <div className="semi-admin-state">正在验证管理员权限…</div>;
  if (!authorized) return (
    <Card className="semi-admin-auth" title="期货管理">
      <div className="semi-admin-state">需要管理员权限</div>
      <a className="semi-admin-login" href="/login/?next=%2Fadmin%2Ffutures%2F">前往登录</a>
    </Card>
  );

  const enabled = items.filter((item) => item.enabled).length;
  return (
    <div className="semi-futures-admin">
      <div className="semi-admin-heading">
        <div><p className="section-kicker">SEMI DESIGN PILOT</p><h2>期货标的管理</h2><p>用 Semi 组件承载真实 CRUD，保留原有 API、权限与数据契约。</p></div>
        <Button onClick={load} loading={busy}>刷新</Button>
      </div>
      <div className="semi-stat-grid">
        <Card><span>监控中</span><strong>{enabled}</strong></Card>
        <Card><span>已停用</span><strong>{items.length - enabled}</strong></Card>
        <Card><span>全部标的</span><strong>{items.length}</strong></Card>
      </div>
      <div className="semi-admin-grid">
        <Card title="新增标的">
          <form className="semi-create-form" onSubmit={add}>
            <label>名称<Input value={name} onChange={setName} placeholder="例如：焦煤" /></label>
            <label>交易所<Input value={exchange} onChange={setExchange} placeholder="例如：大商所" /></label>
            <label>代码（可选）<Input value={code} onChange={setCode} placeholder="例如：JM" maxLength={4} /></label>
            <Button htmlType="submit" theme="solid" type="primary" loading={busy}>添加标的</Button>
          </form>
        </Card>
        <Card title="当前标的" headerExtraContent={<Tag color="blue">{items.length} 个</Tag>}>
          <div className="semi-watchlist">
            {items.length === 0 && <div className="semi-admin-state">暂无标的</div>}
            {items.map((item) => <div className={`semi-watch-row${item.enabled ? '' : ' is-disabled'}`} key={item.code}>
              <div><strong>{item.name || item.code}</strong><small>{item.code} · {item.exchange || '未知交易所'}</small></div>
              <Tag color={item.enabled ? 'green' : 'grey'}>{item.enabled ? '监控中' : '已停用'}</Tag>
              <div className="semi-row-actions"><Button size="small" onClick={() => toggle(item)}>{item.enabled ? '停用' : '启用'}</Button><Button size="small" type="danger" onClick={() => remove(item)}>删除</Button></div>
            </div>)}
          </div>
        </Card>
      </div>
      {message && <p className="semi-admin-message" role="status">{message}</p>}
    </div>
  );
}
