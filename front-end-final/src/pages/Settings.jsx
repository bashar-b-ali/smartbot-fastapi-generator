import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Check,
  Cpu,
  KeyRound,
  Lock,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Server,
  Trash2,
} from 'lucide-react';
import Button from '../components/common/Button';
import { Card, CardBody, CardHeader } from '../components/common/Card';
import Input from '../components/common/Input';
import Modal from '../components/common/Modal';
import { cn } from '../components/common/cn';
import { useChat } from '../contexts/ChatContext';
import { chatService } from '../services/api';

const PROVIDERS = [
  { value: 'custom', label: 'OpenAI-compatible' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'google', label: 'Google' },
  { value: 'ollama', label: 'Ollama' },
];

const PROVIDER_LABEL = Object.fromEntries(PROVIDERS.map((p) => [p.value, p.label]));

const emptyForm = {
  name: '',
  provider: 'custom',
  model_id: '',
  api_key: '',
  api_base_url: '',
  default_max_tokens: 2048,
  temperature: 0.1,
  is_default: false,
};

const selectClass =
  'h-11 w-full rounded-xl border border-line bg-surface-raised px-3.5 text-sm text-ink outline-none transition-colors hover:border-line-strong focus:border-primary-500 focus:ring-4 focus:ring-primary-500/18';

const Settings = () => {
  const { loadModels, setSelectedModelId } = useChat();
  const [serverModel, setServerModel] = useState(null);
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const editing = useMemo(
    () => models.find((model) => model.id === editingId) || null,
    [models, editingId]
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [picker, custom] = await Promise.all([
        chatService.listModels(),
        chatService.listCustomModels(),
      ]);
      setServerModel((picker || []).find((m) => m.source === 'server') || null);
      setModels(Array.isArray(custom) ? custom : []);
    } catch (err) {
      setError(err.message || 'Failed to load model settings');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const updateForm = (field, value) => {
    setForm((current) => ({ ...current, [field]: value }));
  };

  const resetForm = () => {
    setEditingId(null);
    setForm(emptyForm);
    setError('');
    setModelDialogOpen(false);
  };

  const addModel = () => {
    setEditingId(null);
    setForm(emptyForm);
    setNotice('');
    setError('');
    setModelDialogOpen(true);
  };

  const editModel = (model) => {
    setEditingId(model.id);
    setForm({
      name: model.name || '',
      provider: model.provider || 'custom',
      model_id: model.model_id || '',
      api_key: '',
      api_base_url: model.api_base_url || '',
      default_max_tokens: model.default_max_tokens || 2048,
      temperature: model.temperature ?? 0.1,
      is_default: !!model.is_default,
    });
    setNotice('');
    setError('');
    setModelDialogOpen(true);
  };

  const payloadFromForm = () => {
    const payload = {
      name: form.name.trim(),
      provider: form.provider,
      model_id: form.model_id.trim(),
      api_base_url: form.api_base_url.trim(),
      default_max_tokens: Number(form.default_max_tokens) || 2048,
      temperature: Number(form.temperature),
      is_default: !!form.is_default,
    };
    if (!editing || form.api_key.trim()) payload.api_key = form.api_key.trim();
    return payload;
  };

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const payload = payloadFromForm();
      const saved = editing
        ? await chatService.updateCustomModel(editing.id, payload)
        : await chatService.createCustomModel(payload);
      if (payload.is_default) setSelectedModelId(saved.id);
      await Promise.all([refresh(), loadModels()]);
      resetForm();
      setNotice(editing ? 'Model updated' : 'Model added');
    } catch (err) {
      setError(err.message || 'Could not save model');
    } finally {
      setSaving(false);
    }
  };

  const setDefault = async (model) => {
    setError('');
    setNotice('');
    try {
      await chatService.setCustomModelDefault(model.id);
      setSelectedModelId(model.id);
      await Promise.all([refresh(), loadModels()]);
      setNotice(`${model.name} is now your default`);
    } catch (err) {
      setError(err.message || 'Could not set default model');
    }
  };

  const useServerDefault = async () => {
    setError('');
    setNotice('');
    try {
      await chatService.useServerDefaultModel();
      setSelectedModelId(null);
      await Promise.all([refresh(), loadModels()]);
      setNotice('Server default is active');
    } catch (err) {
      setError(err.message || 'Could not switch to server default');
    }
  };

  const removeModel = async (model) => {
    setError('');
    setNotice('');
    try {
      await chatService.deleteCustomModel(model.id);
      if (editingId === model.id) resetForm();
      await Promise.all([refresh(), loadModels()]);
      setNotice('Model removed');
    } catch (err) {
      setError(err.message || 'Could not remove model');
    }
  };

  return (
    <div className="min-h-[calc(100vh-3.5rem)] bg-surface-muted">
      <div className="max-w-7xl mx-auto px-3 py-5 sm:px-5 lg:px-6">
        <div className="mb-4 flex flex-col gap-3 border-b border-line pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-subtle">
                Configuration
              </p>
              <h1 className="mt-1 text-xl font-semibold tracking-tight text-ink">
                Model Settings
            </h1>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-ink-muted">
                Choose the model used by project chat and generation. Private model keys stay separate from the backend server default.
            </p>
          </div>
            <div className="grid grid-cols-2 gap-2 sm:flex sm:items-center">
              <SummaryPill icon={Server} label="Server" value={serverModel?.model_id || 'env default'} />
              <SummaryPill icon={Cpu} label="Private" value={models.length.toString()} />
              <Button
                variant="secondary"
                size="sm"
                onClick={refresh}
                loading={loading}
                leftIcon={<RefreshCw className="h-4 w-4" />}
              >
                Refresh
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={addModel}
                leftIcon={<Plus className="h-4 w-4" />}
              >
                Add model
              </Button>
            </div>
        </div>

        {(error || notice) && (
          <div
            className={cn(
              'mb-4 rounded-xl border px-4 py-3 text-sm',
              error
                ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300'
                : 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300'
            )}
          >
            {error || notice}
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <section className="space-y-3">
            <Card className="rounded-xl">
              <CardHeader className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
                    <Server className="h-4 w-4 text-primary-600 dark:text-primary-300" />
                    Server default
                  </h2>
                  <p className="mt-1 text-sm text-ink-subtle">
                    Managed in the backend environment. Use this when you do not need a private provider key.
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={useServerDefault}
                  leftIcon={<Server className="h-4 w-4" />}
                >
                  Use default
                </Button>
              </CardHeader>
              <CardBody className="p-4 pt-0">
                <div className="flex items-center gap-3 rounded-xl border border-line bg-surface-muted px-3 py-2.5">
                  <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-surface-raised text-primary-600 dark:text-primary-300">
                    <Server className="h-4 w-4" />
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-ink truncate">
                      {serverModel?.model_id || 'Configured in backend environment'}
                    </p>
                    <p className="text-xs text-ink-subtle truncate">
                      {PROVIDER_LABEL[serverModel?.provider] || serverModel?.provider || 'Ollama'}
                      {serverModel?.is_default ? ' / active default' : ''}
                    </p>
                  </div>
                </div>
              </CardBody>
            </Card>

            <div className="grid gap-3">
              {loading ? (
                <div className="rounded-xl border border-line bg-surface-raised p-5 text-sm text-ink-subtle">
                  Loading models...
                </div>
              ) : models.length === 0 ? (
                <div className="rounded-xl border border-dashed border-line bg-surface-raised p-6 text-sm text-ink-subtle">
                  No private models configured. Add one from the form on the right if your graduation demo uses a custom provider.
                </div>
              ) : (
                models.map((model) => (
                  <div
                    key={model.id}
                    className="rounded-xl border border-line bg-surface-raised p-3 shadow-card transition-smooth hover:border-line-strong"
                  >
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="flex min-w-0 items-center gap-3">
                        <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-surface-muted text-primary-600 dark:text-primary-300">
                          <Cpu className="h-4 w-4" />
                        </span>
                        <div className="min-w-0">
                          <p className="flex items-center gap-2 text-sm font-semibold text-ink">
                            <span className="truncate">{model.name}</span>
                            {model.is_default && (
                              <span className="inline-flex items-center gap-1 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300">
                                <Check className="h-3 w-3" />
                                default
                              </span>
                            )}
                          </p>
                          <p className="text-xs text-ink-subtle truncate">
                            {PROVIDER_LABEL[model.provider] || model.provider} / {model.model_id}
                          </p>
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        {!model.is_default && (
                          <Button size="xs" variant="subtle" onClick={() => setDefault(model)}>
                            Default
                          </Button>
                        )}
                        <Button
                          size="icon-sm"
                          variant="secondary"
                          aria-label="Edit model"
                          onClick={() => editModel(model)}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          size="icon-sm"
                          variant="danger"
                          aria-label="Delete model"
                          onClick={() => removeModel(model)}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>

          <aside>
            <Card className="rounded-xl sticky top-20">
              <CardHeader className="p-4">
                <h2 className="text-base font-semibold text-ink">Model actions</h2>
                <p className="mt-1 text-sm text-ink-subtle">
                  Add a private model or edit one from the list. Forms open in a focused dialog.
                </p>
              </CardHeader>
              <CardBody className="p-4 pt-0">
                <Button
                  fullWidth
                  variant="primary"
                  leftIcon={<Plus className="h-4 w-4" />}
                  onClick={addModel}
                >
                  Add private model
                </Button>
                <div className="mt-3 flex gap-2 rounded-xl border border-line bg-surface-muted px-3 py-2.5 text-xs leading-5 text-ink-muted">
                  <Lock className="mt-0.5 h-4 w-4 flex-shrink-0 text-ink-subtle" />
                  Keys are only needed when you use your own provider account.
                </div>
              </CardBody>
            </Card>
          </aside>
        </div>

        <Modal
          open={modelDialogOpen}
          onClose={() => !saving && resetForm()}
          title={editing ? 'Edit model' : 'Add private model'}
          description={editing ? 'Update model details. Leave API key blank to keep the saved key.' : 'Fill in the provider details for a private chat model.'}
          size="xl"
          footer={
            <>
              <Button variant="secondary" onClick={resetForm} disabled={saving}>
                Cancel
              </Button>
              <Button
                type="submit"
                form="model-settings-form"
                loading={saving}
                leftIcon={editing ? <Save className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
              >
                {editing ? 'Save changes' : 'Add model'}
              </Button>
            </>
          }
        >
          <form id="model-settings-form" className="space-y-4" onSubmit={submit}>
            <Input
              label="Name"
              value={form.name}
              onChange={(event) => updateForm('name', event.target.value)}
              placeholder="Work GPT"
              required
            />

            <div>
              <label className="mb-1.5 block text-sm font-semibold text-ink">
                Provider
              </label>
              <select
                className={selectClass}
                value={form.provider}
                onChange={(event) => updateForm('provider', event.target.value)}
              >
                {PROVIDERS.map((provider) => (
                  <option key={provider.value} value={provider.value}>
                    {provider.label}
                  </option>
                ))}
              </select>
            </div>

            <Input
              label="Model ID"
              value={form.model_id}
              onChange={(event) => updateForm('model_id', event.target.value)}
              placeholder="gpt-4o-mini"
              required
            />

            <Input
              label="API base URL"
              value={form.api_base_url}
              onChange={(event) => updateForm('api_base_url', event.target.value)}
              placeholder="https://api.openai.com/v1"
              hint="Required for OpenAI-compatible custom APIs and remote Ollama."
            />

            <Input
              label="API key"
              type="password"
              value={form.api_key}
              onChange={(event) => updateForm('api_key', event.target.value)}
              placeholder={editing ? 'Leave blank to keep current key' : 'Provider key'}
              leftIcon={<KeyRound className="h-4 w-4" />}
            />
            <div className="flex gap-2 rounded-xl border border-line bg-surface-muted px-3 py-2.5 text-xs leading-5 text-ink-muted">
              <Lock className="mt-0.5 h-4 w-4 flex-shrink-0 text-ink-subtle" />
              Keys are sent to the backend settings endpoint. Do not use shared classroom keys unless your supervisor approved it.
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Input
                label="Max tokens"
                type="number"
                min="1"
                value={form.default_max_tokens}
                onChange={(event) => updateForm('default_max_tokens', event.target.value)}
              />
              <Input
                label="Temperature"
                type="number"
                min="0"
                max="2"
                step="0.1"
                value={form.temperature}
                onChange={(event) => updateForm('temperature', event.target.value)}
              />
            </div>

            <label className="flex items-center gap-2 rounded-xl border border-line bg-surface-muted px-3 py-2.5 text-sm text-ink">
              <input
                type="checkbox"
                checked={form.is_default}
                onChange={(event) => updateForm('is_default', event.target.checked)}
                className="h-4 w-4 rounded border-line text-primary-600 focus:ring-primary-500"
              />
              Use as my default model
            </label>
          </form>
        </Modal>
      </div>
    </div>
  );
};

const SummaryPill = ({ icon: Icon, label, value }) => (
  <div className="rounded-xl border border-line bg-surface-muted px-3 py-2">
    <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-subtle">
      <Icon className="h-3.5 w-3.5" />
      {label}
    </p>
    <p className="mt-1 max-w-[10rem] truncate text-sm font-semibold text-ink">{value}</p>
  </div>
);

export default Settings;
