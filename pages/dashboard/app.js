const ASPECT_RATIOS = ['不指定', '1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9', '21:9'];
const RESOLUTIONS = ['不指定', '1K', '2K', '4K'];

const state = {
  api: null,
  context: null,
  uploads: [],
  activeTaskId: '',
};

function element(tag, attributes = {}, text = '') {
  const node = document.createElement(tag);
  Object.entries(attributes).forEach(([name, value]) => {
    if (name === 'className') node.className = value;
    else if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  });
  if (text) node.textContent = text;
  return node;
}

function setTheme(context) {
  const theme = context?.theme || 'light';
  document.documentElement.dataset.theme = theme;
  document.body.dataset.theme = theme;
}

function toast(message, error = false) {
  const node = document.querySelector('#toast');
  node.textContent = message;
  node.classList.toggle('is-error', error);
  node.classList.add('visible');
  window.setTimeout(() => node.classList.remove('visible'), 3600);
}

async function invoke(action, payload = {}) {
  const result = await state.api.invoke(action, payload);
  return result?.payload ?? result;
}

function selectedValues(id) {
  return Array.from(document.querySelectorAll(`#${id} input:checked`)).map((node) => node.value);
}

function makeSelect(id, values) {
  const select = element('select', { id });
  values.forEach((value) => select.append(element('option', { value }, value)));
  return select;
}

function imageActionRow(image, taskId) {
  const row = element('div', { className: 'result-image-row' });
  row.append(element('span', {}, image.filename || `图片 ${image.index}`));
  if (!image.available) {
    row.append(element('small', {}, '文件已清理'));
    return row;
  }
  const preview = element('button', { className: 'btn subtle', type: 'button' }, '预览');
  preview.addEventListener('click', async () => {
    try {
      const result = await invoke('image.preview.read', { task_id: taskId, image_index: image.index });
      const dialogImage = document.querySelector('#previewImage');
      dialogImage.src = result.data_url;
      dialogImage.alt = result.filename;
      document.querySelector('#previewDialog').showModal();
    } catch (error) {
      toast(error.message || '该图片过大，无法内嵌预览，请下载查看。', true);
    }
  });
  const download = element('button', { className: 'btn primary', type: 'button' }, '下载');
  download.addEventListener('click', async () => {
    try {
      await state.api.download('image.download', { task_id: taskId, image_index: image.index });
    } catch (error) {
      toast(error.message || '下载失败', true);
    }
  });
  row.append(preview, download);
  return row;
}

function renderTaskDetail(task) {
  const panel = document.querySelector('#taskDetail');
  panel.replaceChildren();
  if (!task) {
    panel.append(element('p', {}, '选择一个任务以查看详情。'));
    return;
  }
  panel.append(element('h2', {}, `任务 ${task.task_id}`));
  panel.append(element('p', {}, `${task.status_label || task.status} · ${task.model || '未指定模型'}`));
  panel.append(element('p', {}, task.prompt || task.prompt_summary || '无提示词'));
  if (task.message) panel.append(element('p', {}, task.message));
  if (task.error) panel.append(element('p', { className: 'error-text' }, task.error));
  if (task.active) {
    const cancel = element('button', { className: 'btn subtle', type: 'button' }, '取消任务');
    cancel.addEventListener('click', async () => {
      try {
        await invoke('image.tasks.cancel', { task_id: task.task_id });
        toast('已请求取消任务');
        await loadTasks();
      } catch (error) {
        toast(error.message || '无法取消任务', true);
      }
    });
    panel.append(cancel);
  }
  const results = element('div', { className: 'result-image-list' });
  (task.result_images || []).forEach((image) => results.append(imageActionRow(image, task.task_id)));
  panel.append(results);
}

async function openTask(taskId) {
  try {
    const result = await invoke('image.tasks.detail', { task_id: taskId });
    state.activeTaskId = taskId;
    renderTaskDetail(result.task);
  } catch (error) {
    toast(error.message || '任务不存在', true);
  }
}

function renderTasks(tasks) {
  const list = document.querySelector('#taskList');
  list.replaceChildren();
  if (!tasks.length) {
    list.append(element('p', {}, '暂无任务。'));
    return;
  }
  tasks.forEach((task) => {
    const button = element('button', { className: 'task-card', type: 'button' });
    button.append(
      element('strong', {}, task.task_id),
      element('span', {}, `${task.status_label || task.status} · ${task.result_count || 0} 张结果`),
      element('small', {}, task.prompt_summary || '无提示词'),
    );
    button.addEventListener('click', () => openTask(task.task_id));
    list.append(button);
  });
}

async function loadTasks() {
  try {
    const result = await invoke('image.tasks.list', { limit: 80 });
    renderTasks(result.tasks || []);
    document.querySelector('#taskCount').textContent = `共 ${result.total || 0} 个任务`;
    if (state.activeTaskId) await openTask(state.activeTaskId);
  } catch (error) {
    toast(error.message || '无法加载任务列表', true);
  }
}

function renderUploads() {
  const list = document.querySelector('#uploads');
  list.replaceChildren();
  state.uploads.forEach((upload, index) => {
    const chip = element('span', { className: 'upload-pill' }, upload.filename);
    const remove = element('button', { type: 'button' }, '×');
    remove.addEventListener('click', () => {
      state.uploads.splice(index, 1);
      renderUploads();
    });
    chip.append(remove);
    list.append(chip);
  });
}

async function uploadFiles(files) {
  for (const file of files) {
    try {
      const result = await state.api.upload('image.reference.upload', file, {});
      state.uploads.push(result.payload ?? result);
    } catch (error) {
      toast(`${file.name}: ${error.message || '上传失败'}`, true);
    }
  }
  renderUploads();
}

function renderTemplates(target, items) {
  target.replaceChildren();
  items.forEach((item) => {
    const label = element('label', { className: 'chip' });
    const checkbox = element('input', { type: 'checkbox', value: item.name });
    label.append(checkbox, document.createTextNode(` ${item.name}`));
    target.append(label);
  });
  if (!items.length) target.append(element('small', {}, '暂无可用项'));
}

async function loadState() {
  try {
    const data = await invoke('image.state.read');
    document.querySelector('#modelStatus').textContent = data.current_model || '未配置模型';
    document.querySelector('#queueStatus').textContent = data.queue?.can_accept ? '队列可接收任务' : '队列已满';
    const model = document.querySelector('#model');
    model.replaceChildren(element('option', { value: '' }, '当前模型'));
    (data.available_models || []).forEach((item) => model.append(element('option', { value: item }, item)));
    document.querySelector('#count').value = data.default_image_count || 1;
    document.querySelector('#count').max = data.max_image_count || 1;
    document.querySelector('#aspectRatio').value = data.default_aspect_ratio || '不指定';
    document.querySelector('#resolution').value = data.default_resolution || '不指定';
    renderTemplates(document.querySelector('#presets'), data.presets || []);
    renderTemplates(document.querySelector('#personas'), data.personas || []);
    document.querySelector('#reference').disabled = !data.supports_image_to_image;
  } catch (error) {
    toast(error.message || '无法读取插件状态', true);
  }
}

async function submit(event) {
  event.preventDefault();
  const button = document.querySelector('#submit');
  button.disabled = true;
  try {
    const result = await invoke('image.generate.submit', {
      prompt: document.querySelector('#prompt').value.trim(),
      model: document.querySelector('#model').value,
      image_count: Number(document.querySelector('#count').value || 1),
      aspect_ratio: document.querySelector('#aspectRatio').value,
      resolution: document.querySelector('#resolution').value,
      presets: selectedValues('presets'),
      personas: selectedValues('personas'),
      reference_tokens: state.uploads.map((item) => item.token),
    });
    state.activeTaskId = result.task_id || '';
    state.uploads = [];
    renderUploads();
    toast('生图任务已提交');
    renderTaskDetail(result.task);
    await loadTasks();
  } catch (error) {
    toast(error.message || '提交失败', true);
  } finally {
    button.disabled = false;
  }
}

function buildPage() {
  document.body.replaceChildren();
  const root = element('main', { className: 'dashboard-page' });
  const shell = element('div', { className: 'dashboard-shell' });
  const header = element('header', { className: 'dashboard-header' });
  const headline = element('div', { className: 'dashboard-header-main' });
  headline.append(element('p', { className: 'dashboard-eyebrow' }, 'Plugin Page'), element('h1', { className: 'dashboard-title' }, '通用生图控制台'), element('p', { className: 'dashboard-subtitle' }, '通过受限的 Dashboard Extension 操作提交任务、查看结果和下载图片。'));
  const status = element('div', { className: 'dashboard-header-actions' });
  status.append(element('span', { className: 'dashboard-pill', id: 'modelStatus' }, '加载中'), element('span', { className: 'dashboard-pill', id: 'queueStatus' }, '--'));
  header.append(headline, status);

  const layout = element('div', { className: 'generate-layout' });
  const form = element('form', { className: 'dashboard-card dashboard-card--padded form-panel' });
  form.id = 'generationForm';
  form.append(element('h2', {}, '创建生图任务'));
  const prompt = element('textarea', { id: 'prompt', rows: '8', placeholder: '描述你想生成的画面，也可以只选择预设或人设。' });
  form.append(element('label', { className: 'field full-span' }, '提示词'), prompt);
  const fields = element('div', { className: 'form-grid' });
  const model = makeSelect('model', []);
  const count = element('input', { id: 'count', type: 'number', min: '1', value: '1' });
  const aspect = makeSelect('aspectRatio', ASPECT_RATIOS);
  const resolution = makeSelect('resolution', RESOLUTIONS);
  [[ '模型', model ], [ '生成数量', count ], [ '宽高比', aspect ], [ '分辨率', resolution ]].forEach(([label, control]) => {
    const field = element('label', { className: 'field' }, label);
    field.append(control);
    fields.append(field);
  });
  form.append(fields);
  form.append(element('h3', {}, '预设'), element('div', { className: 'chip-cloud', id: 'presets' }), element('h3', {}, '人设'), element('div', { className: 'chip-cloud', id: 'personas' }));
  const upload = element('input', { id: 'reference', type: 'file', accept: 'image/jpeg,image/png,image/gif,image/webp,image/heic,image/heif', multiple: '' });
  upload.addEventListener('change', (event) => uploadFiles(Array.from(event.target.files || [])));
  form.append(element('label', { className: 'field full-span' }, '参考图'), upload, element('div', { className: 'upload-list', id: 'uploads' }));
  const submitButton = element('button', { className: 'btn primary', id: 'submit', type: 'submit' }, '开始生成');
  form.append(submitButton);
  form.addEventListener('submit', submit);

  const side = element('section', { className: 'dashboard-card dashboard-card--padded detail-panel' });
  side.id = 'taskDetail';
  side.append(element('p', {}, '提交后在此查看任务详情。'));
  layout.append(form, side);

  const tasks = element('section', { className: 'dashboard-card dashboard-card--padded task-panel' });
  const taskHeading = element('div', { className: 'section-heading' });
  taskHeading.append(element('h2', {}, '任务队列'));
  const refresh = element('button', { className: 'btn subtle', type: 'button' }, '刷新');
  refresh.addEventListener('click', loadTasks);
  taskHeading.append(refresh);
  tasks.append(taskHeading, element('p', { id: 'taskCount' }, '加载中'), element('div', { className: 'task-list', id: 'taskList' }));

  const dialog = element('dialog', { id: 'previewDialog', className: 'lightbox-dialog' });
  const close = element('button', { className: 'btn subtle', type: 'button' }, '关闭');
  close.addEventListener('click', () => dialog.close());
  dialog.append(close, element('img', { id: 'previewImage', alt: '生成图片预览' }));
  root.append(shell);
  shell.append(header, layout, tasks);
  document.body.append(root, dialog, element('div', { id: 'toast', className: 'toast', role: 'status' }));
}

async function init() {
  const raw = window.AstrBotPluginPage;
  if (!raw) throw new Error('AstrBot Plugin Page bridge is unavailable');
  state.api = raw;
  state.context = await raw.ready();
  setTheme(state.context);
  raw.onContext?.((context) => {
    state.context = context;
    setTheme(context);
  });
  buildPage();
  await Promise.all([loadState(), loadTasks()]);
  window.setInterval(() => {
    if (!document.hidden) loadTasks();
  }, 3000);
}

init().catch((error) => {
  document.body.textContent = error.message || 'Unable to load image-generation page.';
});
