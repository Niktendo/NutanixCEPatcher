# NutanixCEPatcher
Patches Phoenix ISO to become a Community Edition of Nutanix AHV w/ optional ESXi hypervisor
1. Setup Foundation VM as usual
2. Extract the contents of this repo on your Foundation VM
3. Execute `NutanixCEPatcher.sh` to add/remove this patch
4. Create your desired ISO (https://portal.nutanix.com/page/documents/kbs/details?targetId=kA032000000TUksCAG#Create_ISO_using_Foundation_VM)
5. After first boot press `Ctrl + C` to exit the Installer UI and type `/mnt/iso/install.sh` to launch the advanced CE Installer UI.
