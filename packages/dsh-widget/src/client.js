window.__ModuleLoader__.load({
  id: '@ai4s/dsh-widget',
  factory() {
    const page = { id: 'ai4s.dashboard', title: 'AI4S', url: 'https://ai4s.echobyte.cn:8445/' }

    function apply({ mosaicAppPages }) {
      const registration = mosaicAppPages.register({
        id: page.id,
        title: page.title,
        async resolve(signal) {
          signal.throwIfAborted()
          return { url: page.url }
        },
      })
      return () => registration.dispose()
    }

    return { __esModule: true, inject: ['mosaicAppPages'], apply }
  },
})
