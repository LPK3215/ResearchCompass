const scrollProgress = document.getElementById('scroll-progress');
window.addEventListener('scroll', () => {
  const h = document.documentElement.scrollHeight - window.innerHeight;
  scrollProgress.style.width = (window.scrollY / h * 100) + '%';
});
const navLinks = document.querySelectorAll('.nav-link');
const sections = document.querySelectorAll('section[id]');
const navObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      const id = entry.target.id;
      navLinks.forEach(link => link.classList.toggle('active', link.getAttribute('href') === '#' + id));
    }
  });
}, { rootMargin: '-80px 0px -60% 0px' });
sections.forEach(s => navObserver.observe(s));
navLinks.forEach(link => {
  link.addEventListener('click', (e) => {
    e.preventDefault();
    const target = document.querySelector(link.getAttribute('href'));
    if (target) window.scrollTo({ top: target.getBoundingClientRect().top + window.pageYOffset - 60, behavior: 'smooth' });
  });
});
const backToTop = document.getElementById('backToTop');
window.addEventListener('scroll', () => {
  backToTop.classList.toggle('visible', window.scrollY > window.innerHeight * 0.8);
});
backToTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
const themeToggle = document.getElementById('themeToggle');
if (localStorage.getItem('theme') === 'light') { document.documentElement.setAttribute('data-theme', 'light'); themeToggle.textContent = '☀️'; }
themeToggle.addEventListener('click', () => {
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  if (isLight) { document.documentElement.removeAttribute('data-theme'); themeToggle.textContent = '🌙'; localStorage.setItem('theme', 'dark'); }
  else { document.documentElement.setAttribute('data-theme', 'light'); themeToggle.textContent = '☀️'; localStorage.setItem('theme', 'light'); }
});
