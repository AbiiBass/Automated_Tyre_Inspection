/* ==========================================================================
   SMRT Tutorial Engine — a guided product tour.

   Each page defines window.TUTORIAL_STEPS = [ ... ] before including this
   file. Every step is an object:

     {
       title, text,            // required
       icon: "fa-xyz",         // used only for steps with no target (fallback modal)
       target: () => Element|null,   // optional: real element to spotlight
       placement: "bottom"|"top"|"left"|"right",  // tooltip side, default "bottom"
       demoHtml: "<div>...</div>",   // optional inline illustration
       enter: () => {},        // optional: runs before this step is shown
                                //   (open a modal, expand a card, start a demo…)
       exit: () => {},         // optional: runs when leaving this step
                                //   (close what enter() opened, stop timers…)
     }

   Steps with a resolvable target get a real "spotlight" cut into a dimmed
   backdrop plus a small pointer callout ("coachmark") next to the actual
   element. Steps without one (welcome/closing screens, or anything whose
   real element genuinely isn't on screen) fall back to the centered modal
   markup in _tutorial_modal.html.
   ========================================================================== */

let tutorialStepIndex = 0;
let tutorialActive = false;
let tutorialResizeHandler = null;

function _tutSteps() {
  return window.TUTORIAL_STEPS || [];
}

function openTutorial() {
  tutorialStepIndex = 0;
  tutorialActive = true;
  _ensureCoachmarkDom();
  renderTutorialStep();

  if (!tutorialResizeHandler) {
    tutorialResizeHandler = () => {
      if (tutorialActive) _positionCoachmark();
    };
    window.addEventListener('resize', tutorialResizeHandler);
    window.addEventListener('scroll', tutorialResizeHandler, true);
  }
}

function closeTutorial() {
  const steps = _tutSteps();
  const current = steps[tutorialStepIndex];
  if (current && typeof current.exit === 'function') {
    try { current.exit(); } catch (e) { /* keep closing regardless */ }
  }
  tutorialActive = false;
  document.getElementById('tutorial-overlay').classList.remove('open');
  _hideCoachmark();

  // Safety net: if this page opened anything on the user's behalf while
  // touring (e.g. the Add Vehicle wizard), make sure it's closed so the
  // app is left exactly as the person found it.
  if (typeof closeWizard === 'function') {
    try { closeWizard(); } catch (e) { /* no-op */ }
  }
  if (typeof resetWizardState === 'function') {
    try { resetWizardState(); } catch (e) { /* no-op */ }
  }
}

function tutorialNext() {
  const steps = _tutSteps();
  if (tutorialStepIndex < steps.length - 1) {
    goToTutorialStep(tutorialStepIndex + 1);
  } else {
    closeTutorial();
  }
}

function tutorialBack() {
  if (tutorialStepIndex > 0) {
    goToTutorialStep(tutorialStepIndex - 1);
  }
}

function goToTutorialStep(newIndex) {
  const steps = _tutSteps();
  const current = steps[tutorialStepIndex];
  if (current && typeof current.exit === 'function') {
    try { current.exit(); } catch (e) { /* continue anyway */ }
  }
  tutorialStepIndex = newIndex;
  renderTutorialStep();
}

function renderTutorialStep() {
  const steps = _tutSteps();
  if (steps.length === 0) return;
  const step = steps[tutorialStepIndex];

  if (typeof step.enter === 'function') {
    try { step.enter(); } catch (e) { /* keep going even if a demo hiccups */ }
  }

  // Give the DOM a moment to settle (modals opening, cards expanding, etc.)
  // before we measure anything or decide how to present this step.
  setTimeout(() => {
    const targetEl = typeof step.target === 'function' ? step.target() : null;
    if (targetEl) {
      _showCoachmark(step, targetEl);
    } else {
      _showFallbackModal(step);
    }
  }, 220);
}

/* ---------------------------- Fallback centered modal ---------------------------- */
function _showFallbackModal(step) {
  _hideCoachmark();

  const steps = _tutSteps();
  document.getElementById('tutorial-icon').className = 'fa ' + (step.icon || 'fa-info-circle');
  document.getElementById('tutorial-step-title').textContent = step.title;
  document.getElementById('tutorial-step-text').innerHTML = step.text;
  document.getElementById('tutorial-progress-label').textContent =
    `Step ${tutorialStepIndex + 1} of ${steps.length}`;
  document.getElementById('tutorial-demo-slot').innerHTML = step.demoHtml || '';

  const dotsWrap = document.getElementById('tutorial-dots');
  dotsWrap.innerHTML = '';
  steps.forEach((_, i) => {
    const dot = document.createElement('div');
    dot.className = 'dot' + (i === tutorialStepIndex ? ' active' : '');
    dotsWrap.appendChild(dot);
  });

  const backBtn = document.getElementById('tutorial-back-btn');
  backBtn.style.visibility = tutorialStepIndex === 0 ? 'hidden' : 'visible';

  const nextBtn = document.getElementById('tutorial-next-btn');
  const isLast = tutorialStepIndex === steps.length - 1;
  nextBtn.innerHTML = isLast
    ? '<i class="fa fa-check" aria-hidden="true"></i> Finish'
    : 'Next <i class="fa fa-arrow-right" aria-hidden="true"></i>';

  document.getElementById('tutorial-overlay').classList.add('open');
}

/* ---------------------------- Coachmark (spotlight + pointer) ---------------------------- */
function _ensureCoachmarkDom() {
  if (document.getElementById('tutorial-coachmark')) return;

  const catcher = document.createElement('div');
  catcher.id = 'tutorial-click-catcher';
  catcher.className = 'tutorial-click-catcher';

  const spotlight = document.createElement('div');
  spotlight.id = 'tutorial-spotlight';
  spotlight.className = 'tutorial-spotlight';

  const coachmark = document.createElement('div');
  coachmark.id = 'tutorial-coachmark';
  coachmark.className = 'tutorial-coachmark';
  coachmark.innerHTML = `
    <div class="cm-arrow" id="cm-arrow"></div>
    <div class="cm-header">
      <span class="cm-step-label" id="cm-step-label"></span>
      <button class="cm-close" onclick="closeTutorial()" aria-label="Close tutorial">✕</button>
    </div>
    <div class="cm-title" id="cm-title"></div>
    <div class="cm-text" id="cm-text"></div>
    <div class="cm-demo" id="cm-demo"></div>
    <div class="cm-dots" id="cm-dots"></div>
    <div class="cm-nav">
      <button class="cm-btn cm-btn-outline" id="cm-back-btn" onclick="tutorialBack()">
        <i class="fa fa-arrow-left" aria-hidden="true"></i> Back
      </button>
      <button class="cm-btn cm-btn-primary" id="cm-next-btn" onclick="tutorialNext()">Next</button>
    </div>
    <button class="cm-close-link" onclick="closeTutorial()">
      <i class="fa fa-times-circle" aria-hidden="true"></i> Close Tutorial
    </button>
  `;

  document.body.appendChild(catcher);
  document.body.appendChild(spotlight);
  document.body.appendChild(coachmark);
}

function _showCoachmark(step, targetEl) {
  document.getElementById('tutorial-overlay').classList.remove('open');
  _ensureCoachmarkDom();

  const steps = _tutSteps();
  document.getElementById('cm-step-label').textContent = `Step ${tutorialStepIndex + 1} of ${steps.length}`;
  document.getElementById('cm-title').textContent = step.title;
  document.getElementById('cm-text').innerHTML = step.text;
  document.getElementById('cm-demo').innerHTML = step.demoHtml || '';

  const dotsWrap = document.getElementById('cm-dots');
  dotsWrap.innerHTML = '';
  steps.forEach((_, i) => {
    const dot = document.createElement('div');
    dot.className = 'dot' + (i === tutorialStepIndex ? ' active' : '');
    dotsWrap.appendChild(dot);
  });

  const backBtn = document.getElementById('cm-back-btn');
  backBtn.style.visibility = tutorialStepIndex === 0 ? 'hidden' : 'visible';

  const nextBtn = document.getElementById('cm-next-btn');
  const isLast = tutorialStepIndex === steps.length - 1;
  nextBtn.innerHTML = isLast
    ? '<i class="fa fa-check" aria-hidden="true"></i> Finish'
    : 'Next <i class="fa fa-arrow-right" aria-hidden="true"></i>';

  document.getElementById('tutorial-click-catcher').classList.add('open');
  document.getElementById('tutorial-spotlight').classList.add('open');
  document.getElementById('tutorial-coachmark').classList.add('open');

  targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  // Let the smooth-scroll settle before measuring the final position.
  setTimeout(() => _positionCoachmark(step, targetEl), 260);
  _currentCoachmarkTarget = targetEl;
  _currentCoachmarkStep = step;
}

let _currentCoachmarkTarget = null;
let _currentCoachmarkStep = null;

function _positionCoachmark(step, targetEl) {
  step = step || _currentCoachmarkStep;
  targetEl = targetEl || _currentCoachmarkTarget;
  if (!targetEl || !document.body.contains(targetEl)) return;

  const spotlight = document.getElementById('tutorial-spotlight');
  const coachmark = document.getElementById('tutorial-coachmark');
  const arrow = document.getElementById('cm-arrow');
  if (!spotlight || !coachmark) return;

  const pad = 8;
  const rect = targetEl.getBoundingClientRect();

  spotlight.style.top = (rect.top - pad) + 'px';
  spotlight.style.left = (rect.left - pad) + 'px';
  spotlight.style.width = (rect.width + pad * 2) + 'px';
  spotlight.style.height = (rect.height + pad * 2) + 'px';

  // Decide placement: prefer the requested side, fall back to whichever
  // side has more room.
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const cmWidth = 320;
  let placement = step.placement || 'bottom';

  const spaceBelow = vh - rect.bottom;
  const spaceAbove = rect.top;
  if (placement === 'bottom' && spaceBelow < 200 && spaceAbove > spaceBelow) placement = 'top';
  if (placement === 'top' && spaceAbove < 200 && spaceBelow > spaceAbove) placement = 'bottom';

  coachmark.classList.remove('cm-top', 'cm-bottom', 'cm-left', 'cm-right');
  coachmark.classList.add('cm-' + placement);

  let top, left;
  if (placement === 'bottom') {
    top = rect.bottom + pad + 14;
    left = rect.left + rect.width / 2 - cmWidth / 2;
  } else if (placement === 'top') {
    top = rect.top - pad - 14 - 10; // extra offset, height applied after measuring
    left = rect.left + rect.width / 2 - cmWidth / 2;
  } else if (placement === 'left') {
    top = rect.top + rect.height / 2 - 80;
    left = rect.left - cmWidth - pad - 14;
  } else {
    top = rect.top + rect.height / 2 - 80;
    left = rect.right + pad + 14;
  }

  left = Math.max(12, Math.min(left, vw - cmWidth - 12));

  coachmark.style.left = left + 'px';
  coachmark.style.visibility = 'hidden';
  coachmark.style.top = '0px';

  requestAnimationFrame(() => {
    const cmRect = coachmark.getBoundingClientRect();
    let finalTop = top;
    if (placement === 'top') finalTop = rect.top - pad - 14 - cmRect.height;
    finalTop = Math.max(12, Math.min(finalTop, vh - cmRect.height - 12));
    coachmark.style.top = finalTop + 'px';
    coachmark.style.visibility = 'visible';

    // Point the little arrow back toward the horizontal center of the target.
    const arrowX = Math.max(20, Math.min(rect.left + rect.width / 2 - left, cmRect.width - 20));
    if (arrow) arrow.style.left = arrowX + 'px';
  });
}

function _hideCoachmark() {
  const catcher = document.getElementById('tutorial-click-catcher');
  const spotlight = document.getElementById('tutorial-spotlight');
  const coachmark = document.getElementById('tutorial-coachmark');
  if (catcher) catcher.classList.remove('open');
  if (spotlight) spotlight.classList.remove('open');
  if (coachmark) coachmark.classList.remove('open');
  _currentCoachmarkTarget = null;
  _currentCoachmarkStep = null;
}
