from setuptools import setup
import setup_translate

pkg = 'Extensions.BTDevicesManager'
setup(name='enigma2-plugin-extensions-btdevicesmanager',
       version='1.0',
       description='this is bt devices manger to pair e.x keyboard or mouse',
       package_dir={pkg: 'VUBTDevicesManager'},
       packages=[pkg],
       package_data={pkg: ['images/*.png', '*.png', '*.xml', 'locale/*/LC_MESSAGES/*.mo', 'BTAudioConnect', 'plugin.png', 'setup.xml']},
       cmdclass=setup_translate.cmdclass,  # for translation
      )
