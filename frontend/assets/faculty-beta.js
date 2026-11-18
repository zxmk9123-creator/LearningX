(() => {
  'use strict';

  const api = 'https://p01--learningx-shared-api--6j4w9s7dpfzw.code.run';
  const form = document.querySelector('#faculty-beta');
  const invite = document.querySelector('#faculty-invite');
  const title = document.querySelector('#beta-title');
  const file = document.querySelector('#beta-file');
  const status = document.querySelector('#beta-status');
  const resume = document.querySelector('#beta-resume');
  const review = document.querySelector('#beta-review');
  const candidates = document.querySelector('#beta-candidates');
  const media = document.querySelector('#beta-media');
  const acknowledge = document.querySelector('#beta-ack');
  let token;
  let course;
  let target;
  let uploaded = false;
  let material;
  let analysisCoverage;
  let queued = false;
  let failedJob = false;
  let running = false;

  async function request(path, method = 'GET', body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const headers = {};
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      if (token) headers.Authorization = 'Bearer ' + token;
      const response = await fetch(api + path, {
        method, headers, signal: controller.signal, credentials: 'omit',
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) {
        if (response.status === 401 && !token) throw new Error('베타 초대 코드를 확인해 주세요.');
        if (response.status === 401) {
          token = undefined;
          invite.required = true;
          throw new Error('세션이 만료됐습니다. 초대 코드를 다시 입력해 주세요.');
        }
        if (response.status === 503) throw new Error('현재 공유 서버에서 이 작업을 처리할 수 없습니다.');
        throw new Error('서버 요청에 실패했습니다. (' + response.status + ')');
      }
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  }

  async function authenticate() {
    if (token) return;
    const value = invite.value.trim();
    if (!/^[A-Za-z0-9_-]{43}$/.test(value)) throw new Error('교수자 베타 초대 코드를 입력해 주세요.');
    ({ access_token: token } = await request('/v1/beta/faculty/session', 'POST', { code: value }));
    invite.value = '';
    invite.required = false;
  }

  async function putFile(documentFile) {
    const destination = new URL(target.upload_url);
    if (destination.protocol !== 'https:') throw new Error('업로드 주소를 확인할 수 없습니다.');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 180000);
    try {
      const response = await fetch(destination, {
        method: 'PUT', headers: target.headers, body: documentFile,
        signal: controller.signal, credentials: 'omit', redirect: 'error',
      });
      if (!response.ok) {
        if ([401, 403].includes(response.status)) target = undefined;
        throw new Error('교안 전송에 실패했습니다. 다시 시도해 주세요.');
      }
      uploaded = true;
    } finally {
      clearTimeout(timer);
    }
  }

  async function waitForAnalysis(path) {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const job = await request(path);
      if (job.status === 'READY' && job.material_id) {
        analysisCoverage = job.analysis_coverage;
        return job.material_id;
      }
      if (job.status === 'FAILED') {
        failedJob = true;
        throw new Error('교안 분석에 실패했습니다. 이어서 진행을 눌러 다시 시도해 주세요.');
      }
      status.textContent = job.status === 'RUNNING' ? '교안 분석 중' : '분석 대기 중';
      await new Promise(resolve => setTimeout(resolve, 5000));
    }
    throw new Error('아직 분석 중입니다. 이어서 진행을 눌러 상태를 확인할 수 있습니다.');
  }

  async function showReview(documentFile) {
    const data = await request('/v1/materials/' + encodeURIComponent(material) + '/candidates');
    candidates.replaceChildren();
    for (const item of data.eligible_candidates) {
      const row = document.createElement('label');
      row.style.display = 'block';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = JSON.stringify({
        page: item.page, block_id: item.block_id, block_sha256: item.block_sha256,
      });
      row.append(input, document.createTextNode(' ' + item.page + '쪽 · ' + item.preview));
      candidates.append(row);
    }
    media.hidden = !/\.pptx$/i.test(documentFile.name);
    acknowledge.required = !media.hidden;
    review.hidden = !data.eligible_candidates.length;
    const visual = analysisCoverage?.vision;
    const visualNotice = visual?.completed_jobs > 0
      ? '이미지·도표 ' + visual.completed_jobs + '건을 추가 분석했습니다. 해석 결과는 아직 검증되지 않았습니다.'
      : '이미지·도표 해석은 수행되지 않았습니다.';
    status.textContent = review.hidden ? '승인 가능한 교안 근거가 없습니다. ' + visualNotice
      : '텍스트 추출 완료 · ' + visualNotice + ' 승인할 근거를 검토해 주세요.';
  }

  async function start(event) {
    event.preventDefault();
    if (running || !form.reportValidity()) return;
    const documentFile = file.files[0];
    if (!/\.(pdf|pptx)$/i.test(documentFile.name) || !documentFile.size || documentFile.size > 100 * 1024 * 1024) {
      status.textContent = '100MB 이하의 비어 있지 않은 PDF 또는 PPTX를 선택해 주세요.';
      return;
    }
    running = true;
    resume.hidden = true;
    form.querySelector('button[type=submit]').disabled = true;
    title.readOnly = true;
    file.disabled = true;
    try {
      status.textContent = '베타 접속 확인 중';
      await authenticate();
      if (!course) course = await request('/v1/courses', 'POST', { title: title.value.trim() });
      const uploads = '/v1/courses/' + encodeURIComponent(course.id) + '/uploads';
      if (!target) target = await request(uploads + '/initiate', 'POST', {
        filename: documentFile.name, size_bytes: documentFile.size,
      });
      const path = uploads + '/' + encodeURIComponent(target.upload_id);
      if (!uploaded) {
        status.textContent = '교안 업로드 중';
        await putFile(documentFile);
      }
      if (failedJob) {
        await request(path + '/retry', 'POST');
        failedJob = false;
      } else if (!queued) {
        const job = await request(path);
        if (job.status === 'AWAITING_UPLOAD') await request(path + '/complete', 'POST');
        queued = true;
      }
      if (!material) material = await waitForAnalysis(path);
      await showReview(documentFile);
    } catch (error) {
      status.textContent = error instanceof TypeError || error.name === 'AbortError'
        ? '연결이 끊겼거나 응답 시간이 초과됐습니다. 다시 시도해 주세요.'
        : error.message;
      resume.hidden = !course;
      form.querySelector('button[type=submit]').disabled = Boolean(course);
      if (!course) {
        title.readOnly = false;
        file.disabled = false;
      }
    } finally {
      running = false;
    }
  }

  form.addEventListener('submit', start);
  resume.addEventListener('click', start);
  review.addEventListener('submit', async event => {
    event.preventDefault();
    if (running) return;
    const items = Array.from(candidates.querySelectorAll('input:checked'), input => JSON.parse(input.value));
    if (!items.length || items.length > 1000) {
      status.textContent = '승인할 근거를 1개 이상, 최대 1,000개 선택해 주세요.';
      return;
    }
    running = true;
    const button = review.querySelector('button[type=submit]');
    button.disabled = true;
    try {
      await authenticate();
      const result = await request('/v1/materials/' + encodeURIComponent(material) + '/approve', 'POST', {
        items, acknowledge_unprocessed_media: acknowledge.checked,
      });
      review.hidden = true;
      status.textContent = result.approved_count + '개 교안 근거 승인 완료';
    } catch (error) {
      status.textContent = error.message;
    } finally {
      running = false;
      button.disabled = false;
    }
  });
})();
