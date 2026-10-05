/* ==========================================================================
   SMRT Tutorial Engine — generic step-through walkthrough.
   Each page defines window.TUTORIAL_STEPS = [{icon, title, text}, ...]
   before including this file.
   ========================================================================== */

let tutorialStepIndex = 0;

function openTutorial() {
  tutorialStepIndex = 0;
  renderTutorialStep();
  document.getElementById('tutorial-overlay').classList.add('open');
}

function closeTutorial() {
  document.getElementById('tutorial-overlay').classList.remove('open');
}

function renderTutorialStep() {
  const steps = window.TUTORIAL_STEPS || [];
  if (steps.length === 0) return;
  const step = steps[tutorialStepIndex];

  document.getElementById('tutorial-icon').className = 'fa ' + step.icon;
  document.getElementById('tutorial-step-title').textContent = step.title;
  document.getElementById('tutorial-step-text').innerHTML = step.text;
  document.getElementById('tutorial-progress-label').textContent =
    `Step ${tutorialStepIndex + 1} of ${steps.length}`;

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

  document.querySelector('.tutorial-body').scrollTop = 0;
}

function tutorialNext() {
  const steps = window.TUTORIAL_STEPS || [];
  if (tutorialStepIndex < steps.length - 1) {
    tutorialStepIndex++;
    renderTutorialStep();
  } else {
    closeTutorial();
  }
}

function tutorialBack() {
  if (tutorialStepIndex > 0) {
    tutorialStepIndex--;
    renderTutorialStep();
  }
}
