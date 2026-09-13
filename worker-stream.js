self.onmessage = async (e) => {
  const { file } = e.data;
  try {
    self.postMessage({ type: 'progress', percent: 2, text: 'Worker: 读取文件中...' });
    const text = await file.text();
    self.postMessage({ type: 'progress', percent: 60, text: 'Worker: JSON.parse 中...' });
    const data = JSON.parse(text);
    self.postMessage({ type: 'progress', percent: 95, text: 'Worker: 完成' });
    self.postMessage({ type: 'result', data });
  } catch (err) {
    self.postMessage({ type: 'error', message: err.message });
  }
};